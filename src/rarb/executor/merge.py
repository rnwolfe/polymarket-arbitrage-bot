"""
On-chain merge module for Polymarket arbitrage.

Merges equal amounts of YES + NO outcome tokens back into USDC collateral.
This allows immediate capital release after arbitrage instead of waiting
for market resolution.

Uses the Gnosis Conditional Tokens Framework (CTF) mergePositions function.
"""

import asyncio
from decimal import Decimal
from typing import Optional, Tuple

from web3 import Web3
from web3.exceptions import ContractLogicError

from rarb.config import get_settings
from rarb.utils.logging import get_logger

log = get_logger(__name__)

# Contract addresses (Polygon Mainnet)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1cfB2dFAa1ECe4A4E8D4eE3Bfe31c7bBe"  # USDC.e on Polygon

# Minimal ABI for mergePositions
CTF_ABI = [
    {
        "name": "mergePositions",
        "type": "function",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


def _get_web3() -> Web3:
    """Get Web3 instance with configured RPC."""
    settings = get_settings()
    return Web3(Web3.HTTPProvider(settings.polygon_rpc_url))


def merge_positions_sync(
    private_key: str,
    condition_id: str,
    amount: Decimal,
    neg_risk: bool = False,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Merge YES + NO tokens back into USDC (synchronous).

    This converts equal amounts of both outcome tokens back into
    the collateral (USDC), realizing the arbitrage profit immediately.

    Args:
        private_key: Wallet private key (with 0x prefix)
        condition_id: The market's condition ID (hex string)
        amount: Amount of EACH token to merge (in token units, not wei)
        neg_risk: Whether this is a neg_risk market

    Returns:
        Tuple of (transaction receipt or None, error message or None)
    """
    try:
        w3 = _get_web3()
        account = w3.eth.account.from_key(private_key)
        
        ctf_contract = w3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS),
            abi=CTF_ABI,
        )

        # Parameters for binary market
        # parentCollectionId is 0x0 for top-level markets
        parent_collection_id = b'\x00' * 32
        
        # Partition: [1, 2] for binary markets (bitmask for each outcome)
        partition = [1, 2]
        
        # Convert amount to USDC decimals (6)
        # Amount is the number of shares, each share represents $1 of collateral
        amount_wei = int(amount * Decimal("1000000"))
        
        # Ensure condition_id is proper bytes32
        if condition_id.startswith("0x"):
            condition_id_bytes = bytes.fromhex(condition_id[2:])
        else:
            condition_id_bytes = bytes.fromhex(condition_id)
        
        # Pad to 32 bytes if needed
        condition_id_bytes = condition_id_bytes.rjust(32, b'\x00')

        log.info(
            "Executing merge",
            condition_id=condition_id[:20] + "...",
            amount=f"{amount:.4f}",
            amount_wei=amount_wei,
        )

        # Build transaction
        tx = ctf_contract.functions.mergePositions(
            Web3.to_checksum_address(USDC_ADDRESS),
            parent_collection_id,
            condition_id_bytes,
            partition,
            amount_wei,
        ).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 200000,
            'maxFeePerGas': w3.eth.gas_price * 2,
            'maxPriorityFeePerGas': w3.to_wei(30, 'gwei'),
        })

        # Sign and send
        signed_tx = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        
        log.info("Merge transaction sent", tx_hash=tx_hash.hex())
        
        # Wait for receipt
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        
        if receipt.status == 1:
            log.info(
                "Merge successful",
                tx_hash=tx_hash.hex(),
                gas_used=receipt.gasUsed,
            )
            return dict(receipt), None
        else:
            log.error("Merge transaction reverted", tx_hash=tx_hash.hex())
            return None, "transaction_reverted"

    except ContractLogicError as e:
        error_str = str(e).lower()
        if "insufficient balance" in error_str:
            log.error("Merge failed - insufficient token balance", error=str(e))
            return None, "insufficient_balance"
        log.error("Merge contract error", error=str(e))
        return None, "contract_error"
    
    except Exception as e:
        error_str = str(e).lower()
        if "insufficient funds" in error_str:
            log.error("Merge failed - insufficient MATIC for gas", error=str(e))
            return None, "insufficient_gas"
        log.error("Merge failed", error=str(e))
        return None, str(e)


async def merge_positions(
    condition_id: str,
    amount: Decimal,
    neg_risk: bool = False,
) -> Tuple[bool, Optional[str]]:
    """
    Merge YES + NO tokens back into USDC (async wrapper).

    Args:
        condition_id: The market's condition ID
        amount: Amount of EACH token to merge
        neg_risk: Whether this is a neg_risk market

    Returns:
        Tuple of (success, error_message)
    """
    settings = get_settings()
    
    if not settings.private_key:
        return False, "No private key configured"
    
    if settings.dry_run:
        log.info(
            "DRY RUN: Would merge positions",
            condition_id=condition_id[:20] + "...",
            amount=f"{amount:.4f}",
        )
        return True, None

    # Run in thread pool to not block event loop
    loop = asyncio.get_event_loop()
    receipt, error = await loop.run_in_executor(
        None,
        merge_positions_sync,
        settings.private_key.get_secret_value(),
        condition_id,
        amount,
        neg_risk,
    )

    if receipt:
        return True, None
    return False, error


async def check_and_merge_position(
    condition_id: str,
    yes_filled_size: Decimal,
    no_filled_size: Decimal,
    neg_risk: bool = False,
    market_title: str = "",
    combined_cost: Optional[Decimal] = None,
    profit_margin: Optional[Decimal] = None,
) -> Tuple[bool, Decimal, Optional[str]]:
    """
    Check if we have matching YES/NO positions and merge them.

    Only merges the minimum of YES and NO filled sizes (the matched amount).

    Args:
        condition_id: Market condition ID
        yes_filled_size: Size of YES tokens acquired
        no_filled_size: Size of NO tokens acquired
        neg_risk: Whether this is a neg_risk market
        market_title: Market question/title for logging
        combined_cost: Combined cost of YES+NO (for P&L tracking)
        profit_margin: Expected profit margin (for P&L tracking)

    Returns:
        Tuple of (success, amount_merged, error_message)
    """
    from datetime import datetime, timezone
    
    # Only merge the matched amount (minimum of both sides)
    merge_amount = min(yes_filled_size, no_filled_size)
    
    if merge_amount <= Decimal("0"):
        log.debug("No matching position to merge", yes=yes_filled_size, no=no_filled_size)
        return False, Decimal("0"), "no_matching_position"
    
    log.info(
        "Merging matched position",
        condition_id=condition_id[:20] + "...",
        merge_amount=f"{merge_amount:.4f}",
        yes_size=f"{yes_filled_size:.4f}",
        no_size=f"{no_filled_size:.4f}",
    )
    
    settings = get_settings()
    success, error = await merge_positions(condition_id, merge_amount, neg_risk)
    
    # Calculate profit for this merge
    profit_usd = None
    if profit_margin is not None:
        # Profit = merge_amount * profit_margin (since $1 payout per share)
        profit_usd = float(merge_amount * profit_margin)
    
    # Record merge in database
    try:
        from rarb.data.repositories import MergeRepository
        
        await MergeRepository.insert(
            timestamp=datetime.now(timezone.utc).isoformat(),
            condition_id=condition_id,
            market_title=market_title[:100] if market_title else "",
            amount=float(merge_amount),
            profit_usd=profit_usd,
            combined_cost=float(combined_cost) if combined_cost else None,
            tx_hash=None,  # TODO: Extract from receipt
            gas_used=None,  # TODO: Extract from receipt
            status="success" if success else "failed",
            error=error if not success else None,
        )
        log.debug("Merge recorded in database", success=success)
    except Exception as e:
        log.debug("Failed to record merge in database", error=str(e))
    
    if success:
        return True, merge_amount, None
    return False, Decimal("0"), error
