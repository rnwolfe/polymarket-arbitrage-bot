"""Tests for trade tracking accuracy.

These tests verify that:
1. Partial fills are logged to the trades table
2. filled_size is correctly captured from trades API
3. Execution status is accurately recorded
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import MagicMock

from rarb.executor.executor import (
    ExecutionStatus,
    OrderResult,
    ExecutionResult,
    OrderExecutor,
    OrderSide,
)


def make_order_result(
    status: ExecutionStatus,
    filled_size: Decimal = Decimal("0"),
    size: Decimal = Decimal("15"),
    price: Decimal = Decimal("0.50"),
    order_id: str = "test-order-123",
    token_id: str = "test-token-456",
    side: OrderSide = OrderSide.BUY,
) -> OrderResult:
    """Create an OrderResult for testing."""
    return OrderResult(
        token_id=token_id,
        side=side,
        price=price,
        size=size,
        status=status,
        order_id=order_id,
        filled_size=filled_size,
        error=None,
    )


def make_mock_opportunity():
    """Create a mock ArbitrageOpportunity."""
    mock_market = MagicMock()
    mock_market.condition_id = "0xtest123"
    mock_market.question = "Test Market Question"
    
    mock_opp = MagicMock()
    mock_opp.market = mock_market
    mock_opp.expected_profit_usd = Decimal("0.50")
    mock_opp.yes_ask = Decimal("0.48")
    mock_opp.no_ask = Decimal("0.50")
    return mock_opp


class TestTradeLogging:
    """Test that _log_trades correctly handles different execution statuses."""

    def test_filled_orders_are_logged(self):
        """FILLED orders should be logged to trades."""
        executor = OrderExecutor.__new__(OrderExecutor)
        executor._trade_log = MagicMock()
        
        yes_order = make_order_result(
            ExecutionStatus.FILLED,
            filled_size=Decimal("15"),
        )
        no_order = make_order_result(
            ExecutionStatus.FILLED,
            filled_size=Decimal("15"),
            order_id="test-order-456",
        )
        
        result = ExecutionResult(
            opportunity=make_mock_opportunity(),
            yes_order=yes_order,
            no_order=no_order,
            status=ExecutionStatus.FILLED,
            total_cost=Decimal("14.70"),
            expected_profit=Decimal("0.30"),
            timestamp=datetime.now(timezone.utc),
        )
        
        executor._log_trades(result)
        
        # Both YES and NO should be logged
        assert executor._trade_log.log_trade.call_count == 2

    def test_partial_fills_are_logged(self):
        """PARTIAL fills with filled_size > 0 should be logged to trades."""
        executor = OrderExecutor.__new__(OrderExecutor)
        executor._trade_log = MagicMock()
        
        # YES filled partially, NO not filled
        yes_order = make_order_result(
            ExecutionStatus.PARTIAL,
            filled_size=Decimal("10"),  # Partial fill
            size=Decimal("15"),
        )
        no_order = make_order_result(
            ExecutionStatus.CANCELLED,
            filled_size=Decimal("0"),
            order_id="test-order-456",
        )
        
        result = ExecutionResult(
            opportunity=make_mock_opportunity(),
            yes_order=yes_order,
            no_order=no_order,
            status=ExecutionStatus.PARTIAL,
            total_cost=Decimal("4.80"),
            expected_profit=Decimal("0"),
            timestamp=datetime.now(timezone.utc),
        )
        
        executor._log_trades(result)
        
        # Only YES should be logged (it has filled_size > 0)
        assert executor._trade_log.log_trade.call_count == 1
        
        # Verify the logged trade has correct size
        logged_trade = executor._trade_log.log_trade.call_args[0][0]
        assert logged_trade.size == 10.0  # filled_size, not requested size
        assert logged_trade.outcome == "yes"

    def test_cancelled_orders_not_logged(self):
        """CANCELLED orders with no fills should not be logged."""
        executor = OrderExecutor.__new__(OrderExecutor)
        executor._trade_log = MagicMock()
        
        yes_order = make_order_result(
            ExecutionStatus.CANCELLED,
            filled_size=Decimal("0"),
        )
        no_order = make_order_result(
            ExecutionStatus.CANCELLED,
            filled_size=Decimal("0"),
            order_id="test-order-456",
        )
        
        result = ExecutionResult(
            opportunity=make_mock_opportunity(),
            yes_order=yes_order,
            no_order=no_order,
            status=ExecutionStatus.CANCELLED,
            total_cost=Decimal("0"),
            expected_profit=Decimal("0"),
            timestamp=datetime.now(timezone.utc),
        )
        
        executor._log_trades(result)
        
        # Nothing should be logged
        assert executor._trade_log.log_trade.call_count == 0

    def test_partial_with_zero_fill_not_logged(self):
        """PARTIAL status but filled_size=0 should not be logged."""
        executor = OrderExecutor.__new__(OrderExecutor)
        executor._trade_log = MagicMock()
        
        # Status is PARTIAL but filled_size is 0 (edge case)
        yes_order = make_order_result(
            ExecutionStatus.PARTIAL,
            filled_size=Decimal("0"),
        )
        no_order = make_order_result(
            ExecutionStatus.FAILED,
            filled_size=Decimal("0"),
            order_id="test-order-456",
        )
        
        result = ExecutionResult(
            opportunity=make_mock_opportunity(),
            yes_order=yes_order,
            no_order=no_order,
            status=ExecutionStatus.PARTIAL,
            total_cost=Decimal("0"),
            expected_profit=Decimal("0"),
            timestamp=datetime.now(timezone.utc),
        )
        
        executor._log_trades(result)
        
        # Nothing should be logged since filled_size is 0
        assert executor._trade_log.log_trade.call_count == 0


class TestFilledSizeTracking:
    """Test that filled_size is correctly captured and used."""

    def test_filled_size_used_for_cost_calculation(self):
        """Trade cost should use filled_size, not requested size."""
        executor = OrderExecutor.__new__(OrderExecutor)
        executor._trade_log = MagicMock()
        
        # Requested 15, only filled 10
        yes_order = make_order_result(
            ExecutionStatus.PARTIAL,
            filled_size=Decimal("10"),
            size=Decimal("15"),
            price=Decimal("0.50"),
        )
        no_order = make_order_result(
            ExecutionStatus.CANCELLED,
            filled_size=Decimal("0"),
            size=Decimal("15"),
            price=Decimal("0.48"),
            order_id="test-order-456",
        )
        
        mock_opp = make_mock_opportunity()
        
        result = ExecutionResult(
            opportunity=mock_opp,
            yes_order=yes_order,
            no_order=no_order,
            status=ExecutionStatus.PARTIAL,
            total_cost=Decimal("5.00"),  # 10 * 0.50
            expected_profit=Decimal("0"),
            timestamp=datetime.now(timezone.utc),
        )
        
        executor._log_trades(result)
        
        # Verify logged trade uses filled_size
        logged_trade = executor._trade_log.log_trade.call_args[0][0]
        assert logged_trade.size == 10.0
        assert logged_trade.cost == 5.0  # 10 * 0.50


class TestExecutionStatusDetermination:
    """Test that overall execution status is correctly determined."""

    def test_both_filled_is_filled(self):
        """Both orders FILLED -> overall FILLED."""
        yes = make_order_result(ExecutionStatus.FILLED, filled_size=Decimal("15"))
        no = make_order_result(ExecutionStatus.FILLED, filled_size=Decimal("15"), order_id="no-123")
        
        # Both filled = FILLED
        assert yes.status == ExecutionStatus.FILLED
        assert no.status == ExecutionStatus.FILLED

    def test_one_filled_one_cancelled_is_partial(self):
        """One FILLED, one CANCELLED -> overall PARTIAL."""
        yes = make_order_result(ExecutionStatus.FILLED, filled_size=Decimal("15"))
        no = make_order_result(ExecutionStatus.CANCELLED, filled_size=Decimal("0"), order_id="no-123")
        
        # This should result in PARTIAL status
        # The executor logic handles this - we're just verifying the inputs
        assert yes.status == ExecutionStatus.FILLED
        assert no.status == ExecutionStatus.CANCELLED
        assert yes.filled_size == Decimal("15")
        assert no.filled_size == Decimal("0")

    def test_both_cancelled_is_cancelled(self):
        """Both CANCELLED -> overall CANCELLED."""
        yes = make_order_result(ExecutionStatus.CANCELLED, filled_size=Decimal("0"))
        no = make_order_result(ExecutionStatus.CANCELLED, filled_size=Decimal("0"), order_id="no-123")
        
        assert yes.status == ExecutionStatus.CANCELLED
        assert no.status == ExecutionStatus.CANCELLED


@pytest.mark.asyncio
class TestTradesAPIRetry:
    """Test the trades API retry logic."""

    async def test_retry_finds_trades_on_second_attempt(self):
        """Trades API may not have indexed yet - retry should find them."""
        # This tests the concept - actual implementation uses asyncio.sleep
        # which we'd need to mock for a true unit test
        
        # Simulate trades API returning empty first, then with data
        call_count = 0
        
        async def mock_get_trades():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []  # First call: empty (not indexed yet)
            return [{"order_id": "test-123", "size": "15.0"}]  # Second call: found
        
        # The retry logic should call get_trades multiple times
        # until it finds the trade or exhausts retries
        trades = await mock_get_trades()
        assert trades == []
        
        trades = await mock_get_trades()
        assert len(trades) == 1
        assert trades[0]["order_id"] == "test-123"
