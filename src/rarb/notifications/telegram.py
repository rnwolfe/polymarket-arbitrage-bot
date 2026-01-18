"""Telegram Bot API notifications."""

from datetime import datetime
from decimal import Decimal
from typing import Optional

import aiohttp

from rarb.config import get_settings
from rarb.utils.logging import get_logger

log = get_logger(__name__)

# Telegram API base URL
TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    """Send notifications via Telegram Bot API."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self.bot_token = bot_token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def is_configured(self) -> bool:
        """Check if Telegram is properly configured."""
        return bool(self.bot_token and self.chat_id)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to Telegram.
        
        Args:
            text: Message text (supports HTML formatting)
            parse_mode: Parse mode (HTML, Markdown, or MarkdownV2)
        
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.is_configured:
            log.debug("Telegram not configured, skipping notification")
            return False

        try:
            session = await self._get_session()
            url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }

            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    log.debug("Telegram notification sent")
                    return True
                else:
                    body = await resp.text()
                    log.warning(
                        "Telegram notification failed",
                        status=resp.status,
                        body=body[:200],
                    )
                    return False

        except Exception as e:
            log.error("Failed to send Telegram notification", error=str(e))
            return False

    async def send_message(self, text: str) -> bool:
        """Alias for send() - provides compatibility with unified notifier interface."""
        return await self.send(text)

    async def notify_arbitrage(
        self,
        market: str,
        yes_ask: Decimal,
        no_ask: Decimal,
        combined: Decimal,
        profit_pct: Decimal,
    ) -> bool:
        """Send arbitrage opportunity notification."""
        profit_display = float(profit_pct) * 100
        text = (
            f"<b>Arbitrage Detected</b>\n\n"
            f"<b>Market:</b> {self._escape_html(market[:80])}\n"
            f"<b>Profit:</b> +{profit_display:.2f}%\n"
            f"<b>YES Ask:</b> ${float(yes_ask):.4f}\n"
            f"<b>NO Ask:</b> ${float(no_ask):.4f}\n"
            f"<b>Combined:</b> ${float(combined):.4f}\n\n"
            f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        )
        return await self.send(text)

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
        emoji = "" if status == "executed" else ""
        text = (
            f"<b>{emoji} Trade {status.title()}</b>\n\n"
            f"<b>Platform:</b> {platform}\n"
            f"<b>Action:</b> {side.upper()} {outcome.upper()}\n"
            f"<b>Price:</b> ${float(price):.4f}\n"
            f"<b>Size:</b> ${float(size):.2f}\n"
            f"<b>Market:</b> {self._escape_html(market[:60])}\n\n"
            f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        )
        return await self.send(text)

    async def notify_error(self, error: str, context: Optional[str] = None) -> bool:
        """Send error notification."""
        text = (
            f"<b>Error Alert</b>\n\n"
            f"<code>{self._escape_html(error)}</code>"
        )
        if context:
            text += f"\n\n<b>Context:</b> {self._escape_html(context)}"
        text += f"\n\n<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        return await self.send(text)

    async def notify_startup(self, mode: str, markets: int = 0) -> bool:
        """Send bot startup notification."""
        settings = get_settings()
        text = (
            f"<b>rarb Bot Started</b>\n\n"
            f"<b>Mode:</b> {mode}\n"
            f"<b>Min Profit:</b> {settings.min_profit_threshold * 100:.1f}%\n"
            f"<b>Max Position:</b> ${settings.max_position_size}\n"
            f"<b>Markets:</b> {markets}\n\n"
            f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        )
        return await self.send(text)

    async def notify_shutdown(self, reason: str = "normal") -> bool:
        """Send bot shutdown notification."""
        text = f"<b>rarb bot shutting down:</b> {self._escape_html(reason)}"
        return await self.send(text)

    async def notify_daily_summary(
        self,
        trades: int,
        volume: float,
        profit: float,
        alerts: int,
    ) -> bool:
        """Send daily summary notification."""
        text = (
            f"<b>Daily Summary</b>\n\n"
            f"<b>Trades:</b> {trades}\n"
            f"<b>Volume:</b> ${volume:.2f}\n"
            f"<b>Profit:</b> ${profit:.2f}\n"
            f"<b>Alerts:</b> {alerts}\n\n"
            f"<i>{datetime.now().strftime('%Y-%m-%d')}</i>"
        )
        return await self.send(text)

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )


# Global notifier instance
_telegram_notifier: Optional[TelegramNotifier] = None


def get_telegram_notifier() -> TelegramNotifier:
    """Get the global Telegram notifier instance."""
    global _telegram_notifier
    if _telegram_notifier is None:
        _telegram_notifier = TelegramNotifier()
    return _telegram_notifier
