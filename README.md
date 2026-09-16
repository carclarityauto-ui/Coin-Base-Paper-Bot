# Multi-Crypto Active Paper Scalper V3

Paper-only Railway bot for BTC-USD, ETH-USD, SOL-USD, and XRP-USD.

V3 is intentionally more active than V2. It scans every 5 seconds, ranks all four markets using short-term momentum, EMA trend, volume, RSI and volatility, and opens the strongest qualifying setup. It still allows only one open position at a time.

## Default $5,000 paper profile
- up to 10% of equity per position
- 5-second market scans
- 10-second entry cooldown
- lower 0.12 entry-score threshold
- 0.35% stop loss
- target = estimated round-trip trading cost + 0.15% paper profit, with a 0.35% minimum gross target
- 5-minute maximum hold
- 2% daily loss shutdown
- paper execution only

## Important fee assumption
V3 defaults to a simulated 0.10% fee per side plus 0.03% slippage per side. Actual exchange fees vary by account, tier, order type and market. Change `FEE_PCT_PER_SIDE` to the fee assumption you want to test. A strategy that appears profitable under unrealistically low simulated costs may lose money with actual costs.

This software does not guarantee profits and contains no real-money order adapter.
