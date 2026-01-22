from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class MarketSnapshot:
    market_id: str
    question: str
    end_date: Optional[str]
    max_incentive_spread: Optional[float]
    min_incentive_size: Optional[float]
    tick_size: float
    tokens: Dict[str, str]


@dataclass
class OrderSnapshot:
    order_id: str
    market_id: str
    token_id: str
    side: str
    price: float
    size: float
    created_at: datetime


@dataclass
class MarketMakerSnapshot:
    updated_at: datetime
    markets: Dict[str, MarketSnapshot]
    order_books: Dict[str, Dict[str, Optional[float]]]
    inventory: Dict[str, Dict[str, float]]
    open_orders: List[OrderSnapshot]
    locked_usdc: float = 0.0


_snapshot: Optional[MarketMakerSnapshot] = None


def update_market_maker_state(snapshot: MarketMakerSnapshot) -> None:
    global _snapshot
    _snapshot = snapshot


def clear_market_maker_state() -> None:
    global _snapshot
    _snapshot = None


def get_market_maker_state() -> Optional[MarketMakerSnapshot]:
    return _snapshot
