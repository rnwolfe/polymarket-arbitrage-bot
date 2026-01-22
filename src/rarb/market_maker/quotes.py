from typing import List, Dict, Optional
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional

from rarb.api.models import Market
from rarb.config import Settings
from rarb.market_maker.types import InventoryState, MarketQuote
from rarb.utils.logging import get_logger

log = get_logger(__name__)


class QuoteEngine:
    """Computes quotes for market making based on order book and inventory."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.default_tick_size = Decimal(str(getattr(settings, "mm_tick_size", 0.01)))
        self.max_position_size = Decimal(str(getattr(settings, "mm_max_position_size", 100.0)))
        self.default_order_size = Decimal(str(getattr(settings, "mm_order_size", 20.0)))
        self.inventory_soft_limit_pct = Decimal(
            str(getattr(settings, "mm_inventory_soft_limit_pct", 0.7))
        )
        self.max_quote_offset = Decimal(str(getattr(settings, "mm_quote_offset", 0.01)))
        self.min_quote_size_override = getattr(settings, "mm_min_quote_size", None)

    def compute_quotes(
        self,
        market: Market,
        outcome_tokens: Dict[str, str],
        order_books: Dict[str, Dict[str, Optional[float]]],
        inventory: InventoryState,
    ) -> List[MarketQuote]:
        """
        Compute two-sided buy quotes for a market.

        We place BUY orders for both YES and NO tokens.
        """
        quotes = []

        max_spread = getattr(market, "max_incentive_spread", None)
        min_size = getattr(market, "min_incentive_size", None)
        if max_spread is None or min_size is None:
            return quotes

        max_spread = Decimal(str(max_spread))
        min_size = Decimal(str(min_size))
        if max_spread <= 0 or min_size <= 0:
            return quotes

        tick_size = Decimal(str(getattr(market, "tick_size", self.default_tick_size)))
        if tick_size <= 0:
            tick_size = self.default_tick_size

        target_offset = min(self.max_quote_offset, max_spread / Decimal("2"))
        min_quote_size = (
            Decimal(str(self.min_quote_size_override))
            if self.min_quote_size_override is not None
            else min_size
        )

        for outcome, token_id in outcome_tokens.items():
            book = order_books.get(token_id)
            if not book:
                continue

            best_bid = book.get("best_bid")
            best_ask = book.get("best_ask")

            if best_bid is None or best_ask is None:
                continue

            best_bid_dec = Decimal(str(best_bid))
            best_ask_dec = Decimal(str(best_ask))

            if best_ask_dec <= 0 or best_bid_dec <= 0:
                continue

            midpoint = (best_bid_dec + best_ask_dec) / Decimal("2")
            target_price = midpoint - target_offset
            target_price = min(target_price, best_bid_dec)

            if target_price <= 0:
                continue

            target_price = (target_price / tick_size).to_integral_value(rounding=ROUND_DOWN)
            target_price = target_price * tick_size
            if target_price <= 0 or target_price >= best_ask_dec:
                continue

            # Inventory skew adjustment
            current_pos = Decimal(str(inventory.positions.get(token_id, 0.0)))

            # If we have too much of this token, lower our bid price or decrease size
            # For simplicity, we'll adjust the size primarily, but price can also be adjusted.
            # Here we just check max position.
            if current_pos >= self.max_position_size:
                log.debug(
                    "Max position reached for token",
                    token_id=token_id,
                    market_id=market.id,
                )
                continue

            remaining_capacity = self.max_position_size - current_pos
            target_size = min(remaining_capacity, self.default_order_size)

            if target_size < min_quote_size:
                continue

            if current_pos > self.max_position_size * self.inventory_soft_limit_pct:
                target_price = max(tick_size, target_price - tick_size)
                target_size = max(min_quote_size, target_size / Decimal("2"))

            quotes.append(
                MarketQuote(
                    market_id=market.id,
                    token_id=token_id,
                    side="BUY",
                    price=float(target_price),
                    size=float(target_size),
                )
            )

        return quotes
