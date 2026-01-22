import asyncio
from datetime import datetime
from typing import Dict, List
from rarb.executor.async_clob import AsyncClobClient
from rarb.market_maker.types import OpenOrder, MarketQuote
from rarb.utils.logging import get_logger
from rarb.config import Settings

log = get_logger(__name__)


class OrderManager:
    """Manages open orders, cancellation, and placement."""

    def __init__(self, clob_client: AsyncClobClient, settings: Settings):
        self.clob_client = clob_client
        self.settings = settings
        self.dry_run = settings.dry_run
        self.order_staleness_seconds = getattr(settings, "mm_order_staleness_seconds", 30)
        self.price_tolerance = getattr(settings, "mm_price_tolerance", 0.002)
        self.size_tolerance = getattr(settings, "mm_size_tolerance", 1.0)

        self._open_orders: Dict[str, OpenOrder] = {}  # order_id -> OpenOrder

    async def sync_with_exchange(self) -> None:
        """Fetch current open orders from exchange to sync state."""
        try:
            exchange_orders = await self.clob_client.get_orders()

            # Reset internal state
            new_orders: Dict[str, OpenOrder] = {}
            for o in exchange_orders:
                # Polmarket API structure for orders:
                # { "orderID": "...", "asset": "token_id", "market": "market_id", "price": "0.5", "size": "100", ... }
                order_id = o.get("orderID")
                if not order_id:
                    continue

                new_orders[order_id] = OpenOrder(
                    order_id=order_id,
                    market_id=o.get("market", ""),
                    token_id=o.get("asset", ""),
                    side=o.get("side", "BUY"),
                    price=float(o.get("price", 0)),
                    size=float(o.get("size", 0)),
                    filled_size=float(o.get("filled", 0)),
                    status="open",
                )
            self._open_orders = new_orders
            log.debug("Open orders synced", count=len(self._open_orders))
        except Exception as e:
            log.error("Failed to sync orders", error=str(e))

    async def cancel_all(self) -> None:
        """Cancel all managed open orders."""
        if not self._open_orders:
            return

        if self.dry_run:
            log.info("Dry run: Would cancel all orders", count=len(self._open_orders))
            self._open_orders = {}
            return

        try:
            await self.clob_client.cancel_all()
            self._open_orders = {}
            log.info("All orders cancelled")
        except Exception as e:
            log.error("Failed to cancel all orders", error=str(e))

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order."""
        if order_id not in self._open_orders:
            return False

        if self.dry_run:
            log.info("Dry run: Would cancel order", order_id=order_id)
            self._open_orders.pop(order_id, None)
            return True

        try:
            success = await self.clob_client.cancel_order(order_id)
            if success:
                self._open_orders.pop(order_id, None)
                log.debug("Order cancelled", order_id=order_id)
            return success
        except Exception as e:
            log.error("Failed to cancel order", order_id=order_id, error=str(e))
            return False

    async def place_quotes(self, quotes: List[MarketQuote]) -> None:
        """Place new quotes and cancel stale ones for the same tokens."""
        for quote in quotes:
            # Cancel existing orders for this token before placing new one
            # Simple replacement logic: one order per token
            existing_ids = [
                oid for oid, o in self._open_orders.items() if o.token_id == quote.token_id
            ]

            for oid in existing_ids:
                # Check if price/size is same, maybe keep it?
                existing = self._open_orders[oid]
                if (
                    abs(existing.price - quote.price) < self.price_tolerance
                    and abs(existing.size - quote.size) < self.size_tolerance
                ):
                    # Check staleness
                    age = (datetime.now() - existing.created_at).total_seconds()
                    if age < self.order_staleness_seconds:
                        continue  # Keep it

                await self.cancel_order(oid)

            # Place new order
            if self.dry_run:
                log.info(
                    "Dry run: Placing quote",
                    market_id=quote.market_id,
                    token_id=quote.token_id,
                    price=quote.price,
                    size=quote.size,
                )
                # We don't have a real order_id, so we make one for dry run tracking
                fake_id = f"dry_{quote.token_id}_{int(datetime.now().timestamp())}"
                self._open_orders[fake_id] = OpenOrder(
                    order_id=fake_id,
                    market_id=quote.market_id,
                    token_id=quote.token_id,
                    side=quote.side,
                    price=quote.price,
                    size=quote.size,
                )
                continue

            try:
                resp = await self.clob_client.submit_order(
                    token_id=quote.token_id,
                    side=quote.side,
                    price=quote.price,
                    size=quote.size,
                    order_type="GTC",
                    post_only=True,
                )

                order_id = resp.get("orderID")
                if order_id:
                    self._open_orders[order_id] = OpenOrder(
                        order_id=order_id,
                        market_id=quote.market_id,
                        token_id=quote.token_id,
                        side=quote.side,
                        price=quote.price,
                        size=quote.size,
                    )
                    log.info("Quote placed", order_id=order_id, price=quote.price, size=quote.size)
                else:
                    log.error("Failed to place quote: no orderID in response", response=resp)
            except Exception as e:
                log.error("Failed to place quote", token_id=quote.token_id, error=str(e))

    def get_open_orders_for_market(self, market_id: str) -> List[OpenOrder]:
        """Get all managed open orders for a specific market."""
        return [o for o in self._open_orders.values() if o.market_id == market_id]

    def list_open_orders(self) -> List[OpenOrder]:
        """List all open orders managed by the bot."""
        return list(self._open_orders.values())
