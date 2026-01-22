from dataclasses import dataclass, field
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict


@dataclass
class MarketQuote:
    """Market-making quote for a specific market outcome."""

    market_id: str
    token_id: str
    side: str  # "BUY"
    price: float
    size: float
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class OpenOrder:
    """Represents an active order on the exchange."""

    order_id: str
    market_id: str
    token_id: str
    side: str
    price: float
    size: float
    filled_size: float = 0.0
    status: str = "open"
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class InventoryState:
    """Current inventory for a specific market."""

    market_id: str
    positions: Dict[str, float] = field(default_factory=dict)  # token_id -> size
    timestamp: datetime = field(default_factory=datetime.now)
