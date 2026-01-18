# Polymarket Arbitrage Bot - Deep Dive Analysis

**Date:** January 18, 2026  
**Analyst:** Antigravity AI

## Executive Summary

After comprehensive analysis, I've identified **critical issues** in three areas:
1. **Execution latency** - Several avoidable bottlenecks add 100-500ms+ to order execution
2. **Liquidity handling** - Overly conservative safety margin (50%) + shallow book analysis causing excessive skips  
3. **Partial fill risk** - The unwind strategy has fundamental timing issues leaving you exposed
4. **Dashboard/Telegram** - Dashboard is NOT auto-launched with bot; Telegram is **completely unimplemented**

---

## 🔴 CRITICAL: Latency Bottlenecks

### Issue 1: Forced Re-warmup Before Every Execution
**File:** `src/rarb/executor/executor.py:1026-1028`
```python
# Ensure connections are warm right before submission
warmup_start = ExecutionTiming.now_ms()
await async_client.warmup(num_connections=2, force=True)  # ❌ ALWAYS forces warmup
```

**Problem:** You have a background keepalive task running every 3s, but then you STILL force a warmup before every execution. This adds **50-300ms latency** per execution.

**Fix:** Only warmup if actually cold:
```python
# Only refresh if connections have been idle too long
if async_client._last_request_time < time.time() - 2.0:  # Cold threshold
    await async_client.warmup(num_connections=2, force=True)
```

---

### Issue 2: Sequential neg_risk + fee_rate API Calls
**File:** `src/rarb/executor/async_clob.py:599-612`

Even though you pre-cache neg_risk on market load, if ANY token isn't in cache (e.g., after market refresh), you make blocking API calls during execution.

**Fix:** Pre-warm the cache more aggressively after market refresh:
```python
# In RealtimeScanner._periodic_market_refresh() after load_markets():
if self._on_markets_loaded:
    await self._on_markets_loaded(markets)  # Already there - but also prefetch fee_rates
```

Add fee_rate prefetching alongside neg_risk:
```python
async def prefetch_market_data(self, token_ids: list[str]) -> None:
    """Pre-fetch both neg_risk AND fee_rate for all tokens."""
    tasks = [self.get_neg_risk(t) for t in token_ids]
    tasks += [self.get_fee_rate_bps(t) for t in token_ids]
    await asyncio.gather(*tasks, return_exceptions=True)
```

---

### Issue 3: Thread Pool Contention for Order Signing
**File:** `src/rarb/executor/async_clob.py:624-638`
```python
sign_tasks = [
    loop.run_in_executor(None, self.sign_order, ...)  # Uses default ThreadPoolExecutor
    for ...
]
```

**Problem:** Default thread pool has limited workers. Under load, signing can queue behind other tasks.

**Fix:** Create a dedicated CPU thread pool for signing:
```python
import concurrent.futures

class AsyncClobClient:
    def __init__(self, ...):
        ...
        self._signing_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="sign")

    async def submit_orders_parallel(self, ...):
        sign_tasks = [
            loop.run_in_executor(self._signing_executor, self.sign_order, ...)
            ...
        ]
```

---

### Issue 4: 300ms Artificial Delay for "Delayed" Orders
**File:** `src/rarb/executor/executor.py:1168`
```python
await asyncio.sleep(0.3)  # 300ms for matching engine
```

**Problem:** FOK orders should resolve immediately. This blanket 300ms wait is unnecessary for most cases.

**Fix:** Poll more aggressively with exponential backoff:
```python
for delay in [0.05, 0.1, 0.15]:  # 50ms, 100ms, 150ms (total 300ms max)
    await asyncio.sleep(delay)
    status = await async_client.get_order(order_id)
    if status in ('filled', 'matched', 'canceled', 'expired'):
        break
```

---

## 🔴 CRITICAL: Liquidity Handling Issues

### Issue 5: 50% Safety Margin is Killing Opportunities
**File:** `src/rarb/bot.py:358-361`
```python
LIQUIDITY_SAFETY_MARGIN = Decimal("0.50")  # Only use 50% of available liquidity
available_size = (raw_available * LIQUIDITY_SAFETY_MARGIN).quantize(...)
```

**Problem:** You're throwing away 50% of every opportunity. If orderbook shows 100 shares available, you only try to take 50.

**Analysis:** This margin exists to account for:
1. Stale orderbook data (WebSocket latency)
2. Other traders taking same opportunity
3. Book depth inaccuracies

**Better approach - Dynamic margin based on latency:**
```python
# Calculate safety margin based on how stale the data is
data_age_ms = (time.time() - alert.timestamp) * 1000
if data_age_ms < 100:
    safety_margin = Decimal("0.80")  # Fresh data - be aggressive
elif data_age_ms < 500:
    safety_margin = Decimal("0.65")
else:
    safety_margin = Decimal("0.50")  # Stale - be conservative
```

---

### Issue 6: Only Looking at Best Ask (No Book Walking)
**File:** `src/rarb/scanner/realtime_scanner.py:382-417`

The scanner only tracks `yes_best_ask_size` and `no_best_ask_size`. If the best ask only has 5 shares but there's 1000 more shares at the next price level (0.01 higher), you miss the real liquidity.

**Problem:** You're seeing "insufficient liquidity" when there's actually plenty at slightly worse prices.

**Fix - Walk the book to calculate available depth:**
```python
def calculate_available_liquidity(
    orderbook: list[OrderLevel], 
    target_price: Decimal,
    max_slippage: Decimal = Decimal("0.02")  # 2 cents
) -> Decimal:
    """Calculate total liquidity available within slippage tolerance."""
    total_size = Decimal("0")
    max_price = target_price + max_slippage
    
    for level in sorted(orderbook, key=lambda x: x.price):
        if level.price > max_price:
            break
        total_size += level.size
    
    return total_size
```

Then use weighted average price for the actual execution.

---

### Issue 7: Min Liquidity Threshold Set Too High
**File:** `src/rarb/config.py:57-61`
```python
min_liquidity_usd: float = Field(
    default=10000.0,  # ❌ $10k minimum market liquidity
)
```

**Problem:** This filters markets at discovery time. Many markets with good arbitrage opportunities have <$10k total liquidity but have sufficient depth at the arb prices.

**Fix:** Lower to $2k-5k, rely on per-opportunity liquidity checks instead:
```python
min_liquidity_usd: float = Field(default=2000.0, ...)
```

---

## 🔴 CRITICAL: Partial Fill / Exposure Issues

### Issue 8: FOK Orders Can Still Partially Fail
**File:** `src/rarb/executor/executor.py:1048`
```python
responses, order_timing = await async_client.submit_orders_parallel(orders)
yes_response, no_response = responses[0], responses[1]
```

**Problem:** Even with parallel submission, there's a race condition. If order A fills and order B fails (rejected, insufficient liquidity on that side), you're exposed.

**The unwind strategy has critical issues:**

**Issue 8a: 5-second Settlement Wait**
**File:** `src/rarb/executor/executor.py:506-512`
```python
settlement_wait = 5.0  # seconds
await asyncio.sleep(settlement_wait)
```

**Problem:** You wait 5 SECONDS before attempting unwind. The arbitrage opportunity price has likely moved significantly. You're selling into a potentially much worse market.

**Fix - Start unwind immediately with settlement retry:**
```python
async def _attempt_unwind_with_retry(self, ...):
    """Attempt unwind immediately, retry if tokens not yet settled."""
    for attempt in range(3):
        try:
            return await self._submit_unwind_order(...)
        except InsufficientBalanceError:
            # Tokens not settled yet
            await asyncio.sleep(2.0)
    return False
```

**Issue 8b: GTC Unwind Orders Can Sit Forever**
**File:** `src/rarb/executor/executor.py:538`
```python
order_type="GTC",  # GTC for better fill chance
```

**Problem:** If unwind order doesn't fill, it just sits on the book. You're still exposed until it fills or you manually cancel.

**Fix - Use aggressive IOC with price improvement loop:**
```python
async def _attempt_aggressive_unwind(self, token_id, size, buy_price, market_name):
    """Aggressive unwind with escalating price cuts."""
    for discount in [0.97, 0.95, 0.92, 0.88]:  # 3%, 5%, 8%, 12% cuts
        unwind_price = round(buy_price * discount, 2)
        response = await async_client.submit_order(
            ..., 
            price=unwind_price,
            order_type="IOC"  # Immediate-or-cancel
        )
        if response.get("status") in ("filled", "matched"):
            return True
        await asyncio.sleep(0.5)  # Brief pause between attempts
    
    # Final fallback: market sell at any price
    return await self._market_sell(token_id, size)
```

---

## 🟡 Dashboard Auto-Launch Issue

**Current State:** Dashboard does NOT auto-launch with `rarb run`

**File:** `src/rarb/cli.py:32-88` (run command) vs `src/rarb/cli.py:992-1008` (dashboard command)

The `run` command only starts the bot. Dashboard is a completely separate command.

**Fix - Add `--with-dashboard` flag to `run`:**
```python
@cli.command()
@click.option("--with-dashboard", is_flag=True, help="Also start the web dashboard")
def run(dry_run, realtime, poll_interval, min_profit, max_position, log_level, with_dashboard):
    ...
    if with_dashboard:
        import threading
        from rarb.dashboard import run_dashboard
        dashboard_thread = threading.Thread(
            target=run_dashboard,
            kwargs={"host": "0.0.0.0", "port": settings.dashboard_port},
            daemon=True
        )
        dashboard_thread.start()
        console.print(f"[dim]Dashboard started at http://0.0.0.0:{settings.dashboard_port}[/dim]")
    
    # Continue with bot startup...
```

**Better approach - Use asyncio task:**
```python
async def run_realtime_bot_with_dashboard() -> None:
    """Run bot and dashboard together."""
    import uvicorn
    from rarb.dashboard.app import app
    
    settings = get_settings()
    
    config = uvicorn.Config(app, host="0.0.0.0", port=settings.dashboard_port, log_level="warning")
    server = uvicorn.Server(config)
    
    async with RealtimeArbitrageBot() as bot:
        await asyncio.gather(
            bot.run(),
            server.serve(),
        )
```

---

## 🔴 Telegram Integration: NOT IMPLEMENTED

**Current State:** Config fields exist but there's **zero implementation**.

**File:** `src/rarb/config.py:100-107`
```python
telegram_bot_token: Optional[str] = Field(default=None, ...)
telegram_chat_id: Optional[str] = Field(default=None, ...)
```

**Files checked:**
- `src/rarb/notifications/` - Only contains `slack.py` and `__init__.py`
- No `telegram.py` exists
- No telegram library in `pyproject.toml`

**Bug Found:** Code calls `notifier.send_message()` but `SlackNotifier` only has `send()` method:
**File:** `src/rarb/bot.py:508`, `src/rarb/executor/executor.py:578`
```python
await notifier.send_message(...)  # ❌ Method doesn't exist on SlackNotifier!
```

**Fix Required - Full Telegram Implementation:**

1. Add dependency to `pyproject.toml`:
```toml
"aiogram>=3.0.0",  # or "python-telegram-bot>=20.0"
```

2. Create `src/rarb/notifications/telegram.py` with TelegramNotifier class

3. Create unified notifier factory that sends to all configured channels

---

## Performance Optimization Summary

| Issue | Impact | Fix Complexity | Latency Saved |
|-------|--------|----------------|---------------|
| Forced re-warmup | High | Easy | 100-300ms |
| Sequential API calls | Medium | Easy | 50-100ms |
| Thread pool contention | Low | Easy | 10-50ms |
| 300ms artificial delay | High | Easy | Up to 250ms |
| 50% safety margin | Very High | Medium | More fills |
| No book walking | Very High | Medium | More fills |
| 5s settlement wait | Critical | Medium | Reduce exposure |
| GTC unwind | Critical | Medium | Reduce exposure |

---

## Recommended Priority Order

1. **🔴 Fix the `send_message` bug** - Code will crash when trying to notify
2. **🔴 Reduce safety margin to dynamic 65-80%** - Immediate impact on fill rate
3. **🔴 Remove forced warmup** - Immediate 100-300ms latency reduction
4. **🔴 Fix unwind timing** - Reduce exposure on partial fills
5. **🔴 Fix 300ms artificial delay** - Use progressive polling
6. **🟡 Add book walking** - Capture more liquidity
7. **🟡 Implement Telegram** - Better monitoring
8. **🟡 Add `--with-dashboard` flag** - Convenience
9. **🟡 Add dedicated signing thread pool** - Reduce contention
10. **🟡 Lower min liquidity threshold** - More opportunities
