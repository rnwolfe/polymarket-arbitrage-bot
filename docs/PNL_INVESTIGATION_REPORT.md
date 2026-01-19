# P&L Tracking Investigation Report

**Date:** January 19, 2026  
**Investigator:** Automated Analysis  
**Severity:** CRITICAL

## Executive Summary

The dashboard P&L display is fundamentally broken, showing **+$10.18 profit** when the actual wallet balance dropped from **$75 to ~$4** over 3 days - a real loss of approximately **$71**.

This represents an **$81 discrepancy** between reported and actual P&L.

## Key Findings

### 1. Zero Successful Arbitrages

| Execution Status | Count | Expected Profit |
|-----------------|-------|-----------------|
| cancelled | 32 | $0.00 |
| failed | 38 | $0.00 |
| partial | 56 | $0.00 |
| **filled** | **0** | **$0.00** |

**Not a single arbitrage completed successfully.** All 56 "partial" executions mean one leg filled but the other failed, requiring emergency unwinding at a loss.

### 2. Missing Transaction Records

| Metric | Expected | Recorded | Gap |
|--------|----------|----------|-----|
| Partial fills requiring unwind | 56 | 33 | **23 missing** |
| Estimated untracked losses | - | - | **~$60+** |

23 partial fill unwinding operations were never recorded to the `closed_positions` table, making their losses invisible.

### 3. P&L Source Confusion

The `closed_positions` table mixes different transaction types:

| Status | Count | P&L | Source |
|--------|-------|-----|--------|
| SOLD | 33 | -$6.93 | Unwound partial fills (arb losses) |
| WON | 2 | +$18.24 | Resolved market positions (NOT arb) |
| RESOLVED | 1 | +$1.93 | Resolved market position |
| LOST | 2 | -$3.06 | Resolved market positions |
| **TOTAL** | 38 | **+$10.18** | Mixed - misleading |

The +$18.24 from "WON" positions came from pre-existing market bets, not arbitrage profits. The dashboard incorrectly presents this as arbitrage performance.

### 4. Fire-and-Forget Database Bug

In `executor.py`, database writes use `asyncio.create_task()` without awaiting:

```python
# Line 571-584 - Can silently fail!
asyncio.create_task(ClosedPositionRepository.insert(...))
```

If these tasks fail (connection issues, race conditions, etc.), the loss is never recorded and becomes invisible.

### 5. No Ground Truth Balance Tracking

The system has no mechanism to:
- Record initial wallet balance when bot starts
- Track periodic balance snapshots with context
- Calculate TRUE P&L as: `current_balance - initial_balance`

## Root Cause Analysis

```
User starts with $75
    │
    ▼
Bot detects "arbitrage opportunity" (YES + NO < $1)
    │
    ▼
Bot places FOK order for YES side ──► FILLS at $X
    │
    ▼
Bot places FOK order for NO side ──► FAILS (liquidity gone)
    │
    ▼
Bot must unwind YES position at market price
    │
    ├──► Loss recorded (-$0.XX) ──► 33 cases
    │
    └──► Loss NOT recorded ──► 23 cases (BUG!)
    │
    ▼
Dashboard sums closed_positions ──► Shows +$10.18
    │
    ▼
But includes WON bets (+$18.24) not from arbitrage!
    │
    ▼
User sees "profit" while wallet drains to $4
```

## Recommendations

### Immediate Fixes Required

1. **Add Wallet Balance Tracking**
   - Record initial balance when bot starts
   - Periodic snapshots with source context
   - TRUE P&L = current - initial (irrefutable)

2. **Fix Fire-and-Forget Database Writes**
   - Await critical writes (especially loss recording)
   - Add retry logic for failed writes
   - Log warnings when writes fail

3. **Separate P&L Categories in Dashboard**
   - Arbitrage P&L (from merges and arb executions)
   - Position P&L (from resolved markets)
   - TRUE Total P&L (wallet balance change)

4. **Add Loss Warnings**
   - Prominent red banner when balance drops >10%
   - Alert when cumulative losses exceed threshold
   - Daily P&L summary notifications

### Database Schema Changes

```sql
-- New table for balance tracking
CREATE TABLE balance_history (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    usdc_balance REAL NOT NULL,
    positions_value REAL DEFAULT 0,
    total_value REAL NOT NULL,
    source TEXT,  -- 'startup', 'periodic', 'post_trade', 'manual'
    notes TEXT
);

-- Add initial_balance to execution_stats
ALTER TABLE execution_stats ADD COLUMN initial_balance REAL;
ALTER TABLE execution_stats ADD COLUMN balance_start_time TEXT;
```

## Financial Impact

| Metric | Value |
|--------|-------|
| Starting Balance | $75.00 |
| Current Balance | ~$4.32 |
| **Actual Loss** | **-$70.68** |
| Dashboard Reported | +$10.18 |
| **Discrepancy** | **$80.86** |

## Conclusion

The P&L tracking system has critical flaws that hide losses from the user. The combination of:
1. Silent database write failures
2. Missing unwind records  
3. Mixed P&L sources
4. No balance-based ground truth

...created a situation where the user lost $71 while the dashboard showed a $10 profit.

**Money should NEVER be invisible.** The fixes outlined above will ensure every dollar is tracked and accounted for.
