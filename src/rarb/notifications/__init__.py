"""Notifications module for Slack and Telegram alerts.

Provides a unified interface for sending notifications through multiple channels.
"""

from typing import Optional, Protocol
from decimal import Decimal

from rarb.notifications.slack import SlackNotifier, get_notifier as get_slack_notifier
from rarb.notifications.telegram import TelegramNotifier, get_telegram_notifier


class Notifier(Protocol):
    """Protocol for notification services."""

    async def send_message(self, text: str) -> bool:
        """Send a simple text message."""
        ...

    async def notify_arbitrage(
        self,
        market: str,
        yes_ask: Decimal,
        no_ask: Decimal,
        combined: Decimal,
        profit_pct: Decimal,
    ) -> bool:
        """Send arbitrage opportunity notification."""
        ...

    async def notify_trade(
        self,
        platform: str,
        market: str,
        side: str,
        outcome: str,
        price: Decimal,
        size: Decimal,
        status: str = "executed",
    ) -> bool:
        """Send trade execution notification."""
        ...

    async def notify_error(self, error: str, context: Optional[str] = None) -> bool:
        """Send error notification."""
        ...

    async def notify_startup(self, mode: str, markets: int = 0) -> bool:
        """Send bot startup notification."""
        ...

    async def notify_shutdown(self, reason: str = "normal") -> bool:
        """Send bot shutdown notification."""
        ...


class MultiNotifier:
    """Send notifications to multiple channels simultaneously."""

    def __init__(self) -> None:
        self._notifiers: list[Notifier] = []
        
        # Add Slack if configured
        slack = get_slack_notifier()
        if slack.webhook_url:
            self._notifiers.append(slack)
        
        # Add Telegram if configured
        telegram = get_telegram_notifier()
        if telegram.is_configured:
            self._notifiers.append(telegram)

    async def send_message(self, text: str) -> bool:
        """Send a simple text message to all channels."""
        if not self._notifiers:
            return False
        results = []
        for notifier in self._notifiers:
            try:
                result = await notifier.send_message(text)
                results.append(result)
            except Exception:
                results.append(False)
        return any(results)

    async def notify_arbitrage(
        self,
        market: str,
        yes_ask: Decimal,
        no_ask: Decimal,
        combined: Decimal,
        profit_pct: Decimal,
    ) -> bool:
        """Send arbitrage notification to all channels."""
        if not self._notifiers:
            return False
        results = []
        for notifier in self._notifiers:
            try:
                result = await notifier.notify_arbitrage(
                    market=market,
                    yes_ask=yes_ask,
                    no_ask=no_ask,
                    combined=combined,
                    profit_pct=profit_pct,
                )
                results.append(result)
            except Exception:
                results.append(False)
        return any(results)

    async def notify_trade(
        self,
        platform: str,
        market: str,
        side: str,
        outcome: str,
        price: Decimal,
        size: Decimal,
        status: str = "executed",
    ) -> bool:
        """Send trade notification to all channels."""
        if not self._notifiers:
            return False
        results = []
        for notifier in self._notifiers:
            try:
                result = await notifier.notify_trade(
                    platform=platform,
                    market=market,
                    side=side,
                    outcome=outcome,
                    price=price,
                    size=size,
                    status=status,
                )
                results.append(result)
            except Exception:
                results.append(False)
        return any(results)

    async def notify_error(self, error: str, context: Optional[str] = None) -> bool:
        """Send error notification to all channels."""
        if not self._notifiers:
            return False
        results = []
        for notifier in self._notifiers:
            try:
                result = await notifier.notify_error(error=error, context=context)
                results.append(result)
            except Exception:
                results.append(False)
        return any(results)

    async def notify_startup(self, mode: str, markets: int = 0) -> bool:
        """Send startup notification to all channels."""
        if not self._notifiers:
            return False
        results = []
        for notifier in self._notifiers:
            try:
                result = await notifier.notify_startup(mode=mode, markets=markets)
                results.append(result)
            except Exception:
                results.append(False)
        return any(results)

    async def notify_shutdown(self, reason: str = "normal") -> bool:
        """Send shutdown notification to all channels."""
        if not self._notifiers:
            return False
        results = []
        for notifier in self._notifiers:
            try:
                result = await notifier.notify_shutdown(reason=reason)
                results.append(result)
            except Exception:
                results.append(False)
        return any(results)

    async def close(self) -> None:
        """Close all notifier sessions."""
        for notifier in self._notifiers:
            close_method = getattr(notifier, 'close', None)
            if close_method is not None:
                try:
                    await close_method()
                except Exception:
                    pass


# Global multi-notifier instance
_multi_notifier: Optional[MultiNotifier] = None


def get_notifier() -> MultiNotifier:
    """Get the global multi-notifier instance.
    
    This returns a notifier that sends to ALL configured channels
    (Slack, Telegram, etc.) simultaneously.
    """
    global _multi_notifier
    if _multi_notifier is None:
        _multi_notifier = MultiNotifier()
    return _multi_notifier


__all__ = [
    "SlackNotifier",
    "TelegramNotifier",
    "MultiNotifier",
    "get_notifier",
    "get_slack_notifier",
    "get_telegram_notifier",
]
