import asyncio
import asyncio
import signal
from datetime import datetime
from typing import Dict, List, Optional

from rarb.api.models import Market
from rarb.config import Settings, get_settings
from rarb.executor.async_clob import AsyncClobClient, create_async_clob_client
from rarb.api.gamma import GammaClient
from rarb.api.websocket import WebSocketClient, OrderBookUpdate
from rarb.market_maker.inventory import InventoryManager
from rarb.market_maker.orders import OrderManager
from rarb.market_maker.quotes import QuoteEngine
from rarb.market_maker.state import (
    MarketMakerSnapshot,
    MarketSnapshot,
    OrderSnapshot,
    clear_market_maker_state,
    update_market_maker_state,
)
from rarb.utils.logging import get_logger

log = get_logger(__name__)


class MarketMakerBot:
    """Orchestrates market making operations."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.clob_client: Optional[AsyncClobClient] = None
        self.gamma_client = GammaClient()
        self.ws_client: Optional[WebSocketClient] = None

        self.inventory_manager: Optional[InventoryManager] = None
        self.quote_engine = QuoteEngine(self.settings)
        self.order_manager: Optional[OrderManager] = None

        self.market_ids: List[str] = getattr(self.settings, "mm_market_ids", [])
        self.refresh_interval = getattr(self.settings, "mm_refresh_interval", 5.0)
        self.cancel_on_stop = getattr(self.settings, "mm_cancel_on_stop", True)

        self._running = False
        self._markets: Dict[str, Market] = {}
        self._market_tokens: Dict[str, Dict[str, str]] = {}
        self._order_books: Dict[str, Dict[str, Optional[float]]] = {}
        self._ws_task: Optional[asyncio.Task] = None
        self._all_token_ids: List[str] = []

    async def initialize(self) -> None:
        """Initialize all components and connections."""
        log.info("Initializing Market Maker Bot")

        self.clob_client = await create_async_clob_client()
        if not self.clob_client:
            raise RuntimeError("Failed to create AsyncClobClient - check credentials")

        await self.clob_client.warmup()

        self.inventory_manager = InventoryManager(self.clob_client)
        self.order_manager = OrderManager(self.clob_client, self.settings)

        # Callbacks for WebSocket
        self.ws_client = WebSocketClient(on_book=self._on_book_update)

        # Market selection
        await self._load_markets()

        # Subscribe to WS
        await self._connect_ws()

        # Initial sync
        await self.inventory_manager.refresh()
        await self.order_manager.sync_with_exchange()
        self._publish_state()

        log.info("Initialization complete")

    def _on_book_update(self, update: OrderBookUpdate) -> None:
        """Handle real-time order book updates."""
        best_bid = float(update.best_bid) if update.best_bid else None
        best_ask = float(update.best_ask) if update.best_ask else None

        self._order_books[update.asset_id] = {"best_bid": best_bid, "best_ask": best_ask}

    async def _load_markets(self) -> None:
        if self.market_ids:
            await self._load_explicit_markets(self.market_ids)
            return

        log.info("Selecting incentive-eligible markets")
        min_liquidity = getattr(self.settings, "mm_min_liquidity", 10000)
        max_markets = getattr(self.settings, "mm_max_markets", 5)
        max_days = getattr(self.settings, "mm_max_days_until_resolution", None)
        active_markets = await self.gamma_client.fetch_all_active_markets(
            min_liquidity=min_liquidity,
            max_days_until_resolution=max_days,
        )

        eligible = [m for m in active_markets if m.is_incentivized]

        eligible.sort(key=lambda m: m.liquidity, reverse=True)
        selected = eligible[:max_markets]
        await self._register_markets(selected)
        log.info("Selected markets", count=len(self._markets))

    async def _load_explicit_markets(self, market_ids: List[str]) -> None:
        markets: List[Market] = []
        for mid in market_ids:
            m_data = await self.gamma_client.get_market(mid)
            if not m_data:
                log.warning("Could not fetch market data", market_id=mid)
                continue

            market = self.gamma_client.parse_market(m_data)
            if not market:
                continue

            markets.append(market)

        await self._register_markets(markets)

    async def _register_markets(self, markets: List[Market]) -> None:
        self._markets = {}
        self._market_tokens = {}
        token_ids: set[str] = set()

        for market in markets:
            if not market:
                continue

            if not market.is_incentivized:
                log.debug("Skipping non-incentivized market", market_id=market.id)
                continue

            token_map = {"YES": market.yes_token.token_id, "NO": market.no_token.token_id}
            self._markets[market.id] = market
            self._market_tokens[market.id] = token_map
            token_ids.update(token_map.values())

        self._all_token_ids = sorted(token_ids)

        if not self._markets:
            raise RuntimeError("No incentive-eligible markets available for market making")

    async def _connect_ws(self) -> None:
        if not self.ws_client:
            return

        if not self._all_token_ids:
            raise RuntimeError("No token IDs available for WebSocket subscription")

        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        await self.ws_client.connect()
        await self.ws_client.subscribe(self._all_token_ids)
        self._ws_task = asyncio.create_task(self.ws_client.listen())

    async def run(self) -> None:
        """Main execution loop."""
        if not self.clob_client:
            await self.initialize()

        if not self.inventory_manager or not self.order_manager:
            raise RuntimeError("Market maker components not initialized")

        self._running = True
        log.info("Market Maker Bot started", interval=self.refresh_interval)

        # Start WS listener in background
        if self.ws_client and not self._ws_task:
            self._ws_task = asyncio.create_task(self.ws_client.listen())

        try:
            while self._running:
                t0 = asyncio.get_event_loop().time()

                # 1. Refresh inventory
                await self.inventory_manager.refresh()

                # 2. Re-sync orders periodically
                await self.order_manager.sync_with_exchange()

                # 3. Compute and place quotes for each market
                for mid, token_map in self._market_tokens.items():
                    market = self._markets.get(mid)
                    if not market:
                        continue

                    inventory = self.inventory_manager.get_market_inventory(mid)
                    if not inventory:
                        from rarb.market_maker.types import InventoryState

                        inventory = InventoryState(market_id=mid)

                    quotes = self.quote_engine.compute_quotes(
                        market=market,
                        outcome_tokens=token_map,
                        order_books=self._order_books,
                        inventory=inventory,
                    )

                    if quotes:
                        await self.order_manager.place_quotes(quotes)

                # Wait for next interval
                if self.ws_client and not self.ws_client.is_connected:
                    log.warning("WebSocket disconnected, reconnecting")
                    await self._connect_ws()

                self._publish_state()

                elapsed = asyncio.get_event_loop().time() - t0
                wait_time = max(0.1, self.refresh_interval - elapsed)
                await asyncio.sleep(wait_time)

        except asyncio.CancelledError:
            log.info("Bot execution cancelled")
        except Exception as e:
            log.error("Error in bot loop", error=str(e), exc_info=True)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Graceful shutdown."""
        log.info("Stopping Market Maker Bot")
        self._running = False

        if self.order_manager and self.cancel_on_stop:
            await self.order_manager.cancel_all()

        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        if self.ws_client:
            await self.ws_client.close()

        if self.clob_client:
            await self.clob_client.close()

        if self.gamma_client:
            await self.gamma_client.close()

        clear_market_maker_state()

        log.info("Bot stopped")

    def _publish_state(self) -> None:
        if not self.inventory_manager or not self.order_manager:
            return

        markets: Dict[str, MarketSnapshot] = {}
        for market_id, market in self._markets.items():
            markets[market_id] = MarketSnapshot(
                market_id=market.id,
                question=market.question,
                end_date=market.end_date.isoformat() if market.end_date else None,
                max_incentive_spread=float(market.max_incentive_spread)
                if market.max_incentive_spread is not None
                else None,
                min_incentive_size=float(market.min_incentive_size)
                if market.min_incentive_size is not None
                else None,
                tick_size=float(market.tick_size),
                tokens=dict(self._market_tokens.get(market_id, {})),
            )

        open_orders = [
            OrderSnapshot(
                order_id=order.order_id,
                market_id=order.market_id,
                token_id=order.token_id,
                side=order.side,
                price=order.price,
                size=order.size,
                created_at=order.created_at,
            )
            for order in self.order_manager.list_open_orders()
        ]

        snapshot = MarketMakerSnapshot(
            updated_at=datetime.utcnow(),
            markets=markets,
            order_books=dict(self._order_books),
            inventory=self.inventory_manager.snapshot(),
            open_orders=open_orders,
        )

        update_market_maker_state(snapshot)

    async def __aenter__(self) -> "MarketMakerBot":
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()


def setup_signal_handlers(bot: MarketMakerBot):
    """Setup handlers for SIGINT and SIGTERM."""
    loop = asyncio.get_event_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))


async def run_market_maker_bot() -> None:
    """Entry point for running the market maker bot."""
    from rarb.config import get_settings
    from rarb.utils.logging import setup_logging

    settings = get_settings()
    setup_logging(settings.log_level)
    async with MarketMakerBot() as bot:
        setup_signal_handlers(bot)
        await bot.run()

    clear_market_maker_state()
