# Latency Analysis & Optimization Guide

## Current Performance Summary

Based on analysis of 126 execution attempts:

| Metric | Fast Executions | Slow Executions |
|--------|-----------------|-----------------|
| Total Time | 600-700ms | 5,000-6,300ms |
| Order Signing | 114ms avg | 589ms avg |
| HTTP Submit | 74ms avg | 539ms avg |
| Occurrence | 56% | 44% |

**Network latency is NOT the bottleneck** - ping to Polymarket is <2ms from Netherlands.

## Execution Pipeline Breakdown

```
Detection → Execute Start → Prefetch → Sign → Submit → Response
              |               |          |       |
              |               16-24ms    114ms   74ms    (fast path)
              |               24ms       589ms   539ms   (slow path)
              |
              └── This gap is where we lose to faster bots
```

## Identified Bottlenecks

### 1. Order Signing Variability (114ms vs 589ms)

**Root Cause:** EIP-712 signing uses `eth_account.messages.encode_typed_data()` which involves:
- JSON serialization
- Keccak256 hashing
- ECDSA signing

When the signing thread pool is cold or contended, this spikes to 500ms+.

**Current Mitigation:**
- Dedicated `ThreadPoolExecutor` for signing (4 workers)
- Pre-warmed on startup

**Potential Improvements:**
- [ ] Pre-compute order templates (only change price/size/salt)
- [ ] Use faster signing library (rust-based `eth-keyfile`)
- [ ] Keep signing threads hot with periodic dummy signs

### 2. HTTP Submit Variability (74ms vs 539ms)

**Root Cause:** Polymarket may be rate-limiting or queuing requests. When both orders hit simultaneously, one may wait for the other.

**Current Mitigation:**
- HTTP/2 connection pooling with keep-alive
- Background ping every 3s to keep connections warm

**Potential Improvements:**
- [ ] Stagger order submission by 10-20ms (let first order clear)
- [ ] Use separate HTTP clients for YES vs NO orders
- [ ] Investigate if Polymarket has undocumented rate limits

### 3. Detection-to-Execution Gap

The time between WebSocket price update and execution start is where professional bots win. Our architecture:

```
WebSocket → Parse → Analyze → Queue → Execute
                              ↑
                              Async task scheduling overhead
```

**Potential Improvements:**
- [ ] Direct callback from WebSocket handler to executor (skip queue)
- [ ] Pre-build order templates for monitored markets
- [ ] Predictive execution (start signing before confirmation)

## Infrastructure Recommendations

### Server Location

**Current:** Netherlands (DigitalOcean)
**Polymarket:** Behind Cloudflare (edge nodes worldwide)

Moving the server won't significantly help because:
1. Cloudflare terminates connections at nearest edge
2. Your ping is already <2ms
3. The bottleneck is compute (signing) not network

**However**, if Polymarket's origin servers are in a specific region (likely US-East), being closer to origin could help for order submission which may bypass Cloudflare caching.

**Recommendation:** Test from NYC or Virginia datacenter to compare.

### Server Specs

Current bottleneck is CPU-bound (signing). Consider:

| Spec | Current | Recommended |
|------|---------|-------------|
| CPU | Shared vCPU | Dedicated CPU |
| Cores | 1-2 | 4+ |
| RAM | 1-2GB | 4GB |

Dedicated CPU prevents noisy neighbor issues that cause signing spikes.

### Network Optimizations

```bash
# Add to /etc/sysctl.conf for lower latency
net.ipv4.tcp_nodelay = 1
net.ipv4.tcp_low_latency = 1
net.core.netdev_budget = 600
```

## Code Optimizations (Future Work)

### Priority 1: Reduce Signing Variance

```python
# Pre-compute static parts of EIP-712 message
# Only recompute: salt, price, size, expiration
class OrderTemplate:
    def __init__(self, token_id, maker, signer):
        self.static_hash = self._compute_static_hash(...)
    
    def sign_fast(self, price, size, salt):
        # Only hash the changing parts
        ...
```

### Priority 2: Parallel Order Paths

```python
# Use separate HTTP clients to avoid head-of-line blocking
self._yes_client = httpx.AsyncClient(...)
self._no_client = httpx.AsyncClient(...)

# Submit truly in parallel
await asyncio.gather(
    self._yes_client.post("/order", ...),
    self._no_client.post("/order", ...),
)
```

### Priority 3: Predictive Execution

```python
# When spread is close to threshold, pre-sign orders
if spread < threshold * 1.2:  # Within 20% of trigger
    self._presigned_orders[market_id] = await self._presign(...)

# On trigger, just submit (skip signing)
if spread < threshold:
    await self._submit_presigned(market_id)
```

## Benchmarking Commands

### Test HTTP Latency
```bash
# Single request
curl -w "Connect: %{time_connect}s, Total: %{time_total}s\n" \
  -o /dev/null -s https://clob.polymarket.com/health

# Multiple requests (check variance)
for i in {1..10}; do
  curl -w "%{time_total}\n" -o /dev/null -s https://clob.polymarket.com/health
done
```

### Test Signing Performance
```python
import time
from eth_account import Account
from eth_account.messages import encode_typed_data

# Benchmark 100 signatures
times = []
for _ in range(100):
    t0 = time.time()
    # ... signing code ...
    times.append(time.time() - t0)

print(f"Avg: {sum(times)/len(times)*1000:.1f}ms")
print(f"P99: {sorted(times)[98]*1000:.1f}ms")
```

## Realistic Expectations

Given current architecture:

| Metric | Current | Optimized | Pro Bots |
|--------|---------|-----------|----------|
| Total Latency | 600-6000ms | 200-400ms | 10-50ms |
| Fill Rate | ~5% | ~15% | ~80% |

To compete with professional arbitrage bots, you would need:
1. Co-located servers (same datacenter as Polymarket origin)
2. Custom signing in Rust/C (sub-1ms)
3. Direct market maker relationships (skip public orderbook)
4. Hardware timestamping and kernel bypass networking

**For a retail bot**, the realistic goal is to catch opportunities that pro bots miss or don't find profitable enough to pursue (higher spreads, lower liquidity).

## Infrastructure Recommendations

### Cloud Provider Comparison (Canada Region)

| Provider | Instance | Specs | Monthly Cost | Best For |
|----------|----------|-------|--------------|----------|
| **DigitalOcean** | CPU-Optimized | 2 vCPU / 4GB | **$42** | Budget + simplicity |
| **Vultr** | Optimized Cloud | 2 vCPU / 8GB | **$60** | Best value/performance |
| **AWS** | c7i.large | 2 vCPU / 4GB | **$71** | Lowest instruction latency |
| **GCP** | c3-highcpu-4 | 4 vCPU / 8GB | **$137** | Overkill for this use case |

### Recommended Setup

**Best Value:** Vultr Optimized Cloud (Toronto) - $60/mo
- Dedicated vCPU (no noisy neighbors)
- 8GB RAM (plenty for order book processing)
- Toronto location (closer to NYC financial infrastructure)

**Budget Option:** DigitalOcean CPU-Optimized (Toronto) - $42/mo
- Dedicated vCPU
- 4GB RAM (sufficient)
- Simple management

### When to Consider AWS/GCP

Move to AWS c7i or GCP c3 when:
1. **Monthly trading volume > $50,000** - the extra latency savings justify cost
2. **Need advanced networking** - VPC peering, dedicated bandwidth
3. **Multi-region deployment** - AWS/GCP have better global presence
4. **Compliance requirements** - enterprise audit trails

For a $100-1000 bankroll test, **DigitalOcean or Vultr is the right choice**.

### Server Configuration

After provisioning, apply these optimizations:

```bash
# /etc/sysctl.conf - Lower TCP latency
net.ipv4.tcp_nodelay = 1
net.ipv4.tcp_low_latency = 1
net.core.netdev_budget = 600

# Apply without reboot
sudo sysctl -p
```

### Region Selection

| Region | Ping to Polymarket | Notes |
|--------|-------------------|-------|
| Toronto (TOR1) | ~15ms | Closest to NYC |
| Montreal | ~20ms | AWS/GCP available |
| Netherlands | ~1-2ms | Cloudflare edge, but origin may be US |

**Recommendation:** Toronto for lowest latency to Polymarket's likely US-East origin servers.

## Revision History

| Date | Change |
|------|--------|
| 2026-01-19 | Initial analysis from 126 executions |
| 2026-01-19 | Added infrastructure recommendations |
| 2026-01-19 | Implemented dual HTTP clients, order staggering, signing warmup |
