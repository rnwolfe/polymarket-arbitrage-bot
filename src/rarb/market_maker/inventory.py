from typing import Dict, List, Optional
from typing import Dict, Optional

from rarb.executor.async_clob import AsyncClobClient
from rarb.market_maker.types import InventoryState
from rarb.utils.logging import get_logger

log = get_logger(__name__)


class InventoryManager:
    """Tracks and refreshes inventory state from the exchange."""

    def __init__(self, clob_client: AsyncClobClient):
        self.clob_client = clob_client
        self._inventory: Dict[str, InventoryState] = {}  # market_id -> InventoryState

    async def refresh(self) -> None:
        """Fetch current positions and update inventory state."""
        try:
            positions = await self.clob_client.get_positions()

            # Reset internal inventory but keep market keys if they were there
            new_inventory: Dict[str, InventoryState] = {}

            for pos in positions:
                # Polmarket API structure for positions:
                # { "asset": "token_id", "size": "100.0", "market": "market_id", ... }
                market_id = pos.get("market")
                token_id = pos.get("asset")
                size = float(pos.get("size", 0))

                if not market_id or not token_id:
                    continue

                if market_id not in new_inventory:
                    new_inventory[market_id] = InventoryState(market_id=market_id)

                new_inventory[market_id].positions[token_id] = size

            self._inventory = new_inventory
            log.debug("Inventory refreshed", market_count=len(self._inventory))

        except Exception as e:
            log.error("Failed to refresh inventory", error=str(e))

    def get_position(self, market_id: str, token_id: str) -> float:
        """Get current size for a specific outcome."""
        state = self._inventory.get(market_id)
        if not state:
            return 0.0
        return state.positions.get(token_id, 0.0)

    def get_market_inventory(self, market_id: str) -> Optional[InventoryState]:
        """Get all positions for a market."""
        return self._inventory.get(market_id)
