# Agent Operations Guide

Common patterns for accessing live bot data, deploying updates, and debugging.

## Server Access

```bash
# SSH to production droplet
ssh can-droplet

# Bot runs in screen session
screen -r rarb        # Attach to session
# Ctrl+A, D to detach
```

## Database Location

The SQLite database is at `~/.rarb/rarb.db` (NOT in the project directory).

```bash
# Quick DB access
ssh can-droplet 'sqlite3 ~/.rarb/rarb.db ".tables"'

# With headers and columns
ssh can-droplet 'sqlite3 -header -column ~/.rarb/rarb.db "SELECT * FROM executions ORDER BY timestamp DESC LIMIT 10"'
```

### Key Tables

| Table | Purpose |
|-------|---------|
| `executions` | Order execution attempts (status, filled sizes, errors) |
| `trades` | Recorded trades (only successful buys currently) |
| `closed_positions` | Resolved/sold positions with realized P&L |
| `balance_history` | Wallet balance snapshots (source of truth for P&L) |
| `execution_stats` | Aggregate stats (attempts, success rate, volume) |
| `alerts` | Arbitrage opportunities detected |
| `merges` | Merge transactions (YES+NO → USDC) |

### Common Queries

```bash
# Execution status breakdown
sqlite3 ~/.rarb/rarb.db "SELECT status, COUNT(*) FROM executions GROUP BY status"

# Total realized P&L from closed positions
sqlite3 ~/.rarb/rarb.db "SELECT SUM(realized_pnl) FROM closed_positions"

# Recent partial fills (potential tracking issues)
sqlite3 -header -column ~/.rarb/rarb.db "SELECT id, timestamp, market, status, yes_filled_size, no_filled_size FROM executions WHERE status='partial' ORDER BY timestamp DESC LIMIT 10"

# Balance history (true P&L source)
sqlite3 -header -column ~/.rarb/rarb.db "SELECT * FROM balance_history ORDER BY timestamp DESC LIMIT 5"
```

## Live Position Data

```bash
# CLI positions command
ssh can-droplet 'cd ~/polymarket-arbitrage-bot && source ~/.pyenv/versions/venv/bin/activate && rarb positions'

# Direct from Polymarket API (most accurate)
ssh can-droplet 'curl -s "https://data-api.polymarket.com/positions?user=0x53d6CcF527C41Ab2c9c3B08594B6879eD05B121f" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d, indent=2))"'

# Check wallet balance
ssh can-droplet 'cd ~/polymarket-arbitrage-bot && source ~/.pyenv/versions/venv/bin/activate && rarb balance'
```

## Bot Logs

```bash
# Get recent logs from screen
ssh can-droplet 'screen -S rarb -p 0 -X hardcopy /tmp/s.log && tail -100 /tmp/s.log'

# Watch live logs (attach to screen)
ssh can-droplet -t 'screen -r rarb'
```

## Deploying Updates

```bash
# 1. Commit and push locally
git add -A && git commit -m "message" && git push origin main

# 2. Pull on server and reinstall
ssh can-droplet 'cd ~/polymarket-arbitrage-bot && git pull && source ~/.pyenv/versions/venv/bin/activate && pip install -e . --quiet'

# 3. Restart bot
ssh can-droplet 'screen -S rarb -X quit; pkill -9 -f rarb; sleep 2; cd ~/polymarket-arbitrage-bot && source ~/.pyenv/versions/venv/bin/activate && screen -dmS rarb rarb run --live --with-dashboard'
```

## Dashboard

- **Port**: 8080 (not 8050)
- **URL**: http://<droplet-ip>:8080

```bash
# Check if dashboard is running
ssh can-droplet 'ss -tlnp | grep 8080'

# Dashboard API endpoints
curl http://<ip>:8080/api/pnl
curl http://<ip>:8080/api/positions
curl http://<ip>:8080/api/trades?limit=20
curl http://<ip>:8080/api/executions?limit=20
```

## Merge Operations

```bash
# Check mergeable positions (dry run)
ssh can-droplet 'cd ~/polymarket-arbitrage-bot && source ~/.pyenv/versions/venv/bin/activate && rarb merge --all'

# Execute merge
ssh can-droplet 'cd ~/polymarket-arbitrage-bot && source ~/.pyenv/versions/venv/bin/activate && rarb merge --all --execute'
```

**Note**: Standard CTF merge only works for `neg_risk=true` markets. Non-neg_risk sports markets use wrapped tokens and require waiting for resolution + redemption.

## Redemption

```bash
# Check redeemable positions
ssh can-droplet 'cd ~/polymarket-arbitrage-bot && source ~/.pyenv/versions/venv/bin/activate && rarb redeem'

# Execute redemption
ssh can-droplet 'cd ~/polymarket-arbitrage-bot && source ~/.pyenv/versions/venv/bin/activate && rarb redeem --execute'
```

## Contract Addresses (Polygon)

| Contract | Address |
|----------|---------|
| CTF (Conditional Tokens) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| NegRiskAdapter | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` |
| NegRisk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` |
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| USDC.e | `0x2791Bca1cfB2dFAa1ECe4A4E8D4eE3Bfe31c7bBe` |

## Wallet

- **Address**: `0x53d6ccf527c41ab2c9c3b08594b6879ed05b121f`
- **Explorer**: https://polygonscan.com/address/0x53d6ccf527c41ab2c9c3b08594b6879ed05b121f

## Troubleshooting

### "not enough balance / allowance" errors
Bot ran out of USDC. Check balance and wait for positions to resolve/merge.

### Partial fills not tracked correctly
Check `executions` table - `yes_filled_size` and `no_filled_size` may be 0 even when orders were placed. The trades API check may have failed.

### Positions showing but not in DB
The `trades` table only records successful buys. Partial fills that result in open positions may not be recorded as trades.
