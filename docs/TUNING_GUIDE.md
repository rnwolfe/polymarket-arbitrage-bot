# Arbitrage Bot Tuning Guide

## Overview

This guide documents how to tune the bot parameters based on observed performance. The goal is to find the sweet spot between:
- **Opportunity frequency** (lower thresholds = more opportunities)
- **Fill rate** (higher thresholds = less competition = more fills)
- **Profit per trade** (higher thresholds = more profit when successful)

## Key Parameters

| Parameter | Description | Trade-off |
|-----------|-------------|-----------|
| `MIN_PROFIT_THRESHOLD` | Minimum profit % to execute | Lower = more opps, more competition |
| `MAX_POSITION_SIZE` | Maximum USD per trade | Higher = more profit, more risk |
| `MIN_LIQUIDITY_USD` | Minimum liquidity required | Higher = better fills, fewer opps |

## Starting Configuration (Recommended)

For a $100 bankroll test:

```bash
MIN_PROFIT_THRESHOLD=0.02      # 2% minimum profit
MAX_POSITION_SIZE=15           # Max $15 per trade
MIN_LIQUIDITY_USD=500          # Require decent liquidity on both sides
```

## Adjustment Scenarios

### Scenario 1: Low Fill Rate (<5%)

**Symptoms:**
- Many opportunities detected
- Most executions result in "partial" status
- One leg fills, other fails with "FOK not filled"
- Frequent emergency unwinds

**Diagnosis:** You're competing against faster bots for thin margins.

**Adjustments:**
```bash
# Raise profit threshold - bigger margins have less competition
MIN_PROFIT_THRESHOLD=0.03      # 3% (was 2%)

# Require more liquidity - thin books cause FOK failures
MIN_LIQUIDITY_USD=1000         # $1000 (was $500)
```

**Expected outcome:** Fewer opportunities, but higher success rate per attempt.

---

### Scenario 2: Very Few Opportunities

**Symptoms:**
- Bot runs for hours with no arbitrage alerts
- Dashboard shows 0 opportunities detected
- Markets are being scanned but nothing qualifies

**Diagnosis:** Thresholds are too strict for current market conditions.

**Adjustments:**
```bash
# Lower profit threshold cautiously
MIN_PROFIT_THRESHOLD=0.015     # 1.5% (was 2%)

# Lower liquidity requirement
MIN_LIQUIDITY_USD=300          # $300 (was $500)
```

**Expected outcome:** More opportunities detected, but expect lower fill rate.

---

### Scenario 3: Consistently Profitable

**Symptoms:**
- TRUE P&L is positive over 24+ hours
- Fill rate is reasonable (>10%)
- No large unexpected losses

**Diagnosis:** Parameters are well-tuned. Consider scaling up.

**Adjustments:**
```bash
# Increase position size to capture more profit
MAX_POSITION_SIZE=25           # $25 (was $15)

# Can slightly lower threshold if fill rate is high
MIN_PROFIT_THRESHOLD=0.018     # 1.8% (was 2%)
```

**Expected outcome:** Higher profit per successful trade, similar fill rate.

---

### Scenario 4: High Volume, Net Loss

**Symptoms:**
- Many trades executing
- Some wins, but losses from unwinds exceed profits
- TRUE P&L trending negative

**Diagnosis:** Partial fills and slippage are eating profits.

**Adjustments:**
```bash
# Raise threshold significantly - only take "sure things"
MIN_PROFIT_THRESHOLD=0.025     # 2.5% (was 2%)

# Smaller position sizes reduce unwind losses
MAX_POSITION_SIZE=10           # $10 (was $15)

# Require much better liquidity
MIN_LIQUIDITY_USD=1000         # $1000 (was $500)
```

**Expected outcome:** Far fewer trades, but each should be more reliable.

---

### Scenario 5: Insufficient Balance Errors

**Symptoms:**
- Alerts showing "insufficient balance" 
- Near-miss alerts in dashboard
- Opportunities skipped due to balance

**Diagnosis:** Position size too large for bankroll, or too many concurrent attempts.

**Adjustments:**
```bash
# Reduce position size relative to bankroll
# Rule of thumb: MAX_POSITION_SIZE < BANKROLL / 5
MAX_POSITION_SIZE=10           # For $50 bankroll
MAX_POSITION_SIZE=20           # For $100 bankroll
MAX_POSITION_SIZE=40           # For $200 bankroll
```

**Expected outcome:** Fewer skipped opportunities, better capital utilization.

---

### Scenario 6: All Orders Cancelled/Timeout

**Symptoms:**
- Executions show "cancelled" status
- Timing data shows long delays
- Orders timing out before filling

**Diagnosis:** Latency issues or network problems.

**Adjustments:**
```bash
# This is an infrastructure issue, not parameter issue
# Consider:
# 1. Check network latency to Polymarket
# 2. Check RPC endpoint performance
# 3. May need co-located server closer to Polymarket infrastructure
```

---

## Parameter Reference Table

### By Bankroll Size

| Bankroll | MAX_POSITION_SIZE | MIN_PROFIT_THRESHOLD | MIN_LIQUIDITY_USD |
|----------|-------------------|----------------------|-------------------|
| $50 | $10 | 0.025 (2.5%) | $500 |
| $100 | $15 | 0.02 (2%) | $500 |
| $200 | $25 | 0.02 (2%) | $750 |
| $500 | $50 | 0.015 (1.5%) | $1000 |
| $1000+ | $100 | 0.01 (1%) | $2000 |

### By Risk Tolerance

| Style | MIN_PROFIT_THRESHOLD | MIN_LIQUIDITY_USD | Notes |
|-------|----------------------|-------------------|-------|
| Conservative | 0.03+ (3%+) | $1000+ | Few trades, high confidence |
| Moderate | 0.02 (2%) | $500 | Balanced approach |
| Aggressive | 0.01 (1%) | $200 | Many trades, higher risk |

## Monitoring Metrics

### Daily Health Check

1. **TRUE P&L**: Is the balance going up or down?
2. **Fill Rate**: `successful / (successful + partial + failed)`
3. **Unwind Rate**: How many partials required emergency unwind?
4. **Opportunity Count**: Are we seeing enough opportunities?

### Warning Signs

| Metric | Warning Threshold | Action |
|--------|-------------------|--------|
| Fill Rate | < 5% | Raise MIN_PROFIT_THRESHOLD |
| TRUE P&L | Down >10% in 24h | Pause and review |
| Unwind Rate | > 50% of partials | Raise MIN_LIQUIDITY_USD |
| Opportunities | 0 in 6 hours | Lower thresholds or check bot health |

## Quick Commands

### Check Current Performance
```bash
ssh poly-droplet "sqlite3 ~/.rarb/rarb.db \"
SELECT 
  status,
  COUNT(*) as count,
  ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM executions), 1) as pct
FROM executions 
GROUP BY status
\""
```

### Check TRUE P&L
```bash
ssh poly-droplet "sqlite3 ~/.rarb/rarb.db \"
SELECT 
  ROUND(initial_balance, 2) as started_with,
  ROUND(current_balance, 2) as now_have,
  ROUND(true_pnl, 2) as profit_loss,
  ROUND(true_pnl_pct, 1) as pct_change
FROM (
  SELECT 
    (SELECT total_value FROM balance_history WHERE source='startup' ORDER BY id ASC LIMIT 1) as initial_balance,
    (SELECT total_value FROM balance_history ORDER BY id DESC LIMIT 1) as current_balance,
    (SELECT total_value FROM balance_history ORDER BY id DESC LIMIT 1) - 
    (SELECT total_value FROM balance_history WHERE source='startup' ORDER BY id ASC LIMIT 1) as true_pnl,
    ((SELECT total_value FROM balance_history ORDER BY id DESC LIMIT 1) - 
     (SELECT total_value FROM balance_history WHERE source='startup' ORDER BY id ASC LIMIT 1)) * 100.0 /
    (SELECT total_value FROM balance_history WHERE source='startup' ORDER BY id ASC LIMIT 1) as true_pnl_pct
)
\""
```

### Update Parameters
```bash
ssh poly-droplet "cd ~/polymarket-arbitrage-bot && nano .env"
# Then restart bot
```

## Revision History

| Date | Change | Reason |
|------|--------|--------|
| 2026-01-19 | Initial document | Post-mortem from $71 loss analysis |
