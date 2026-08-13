# Rapid Multi-Crypto Paper Scalper

Paper-only Railway bot for BTC-USD, ETH-USD, SOL-USD, and XRP-USD.

It scans all configured pairs every few seconds, ranks them by short-term momentum,
trend, volume, RSI and volatility, then trades the strongest qualifying market.
There is no fixed daily trade-count cap, but one position is open at a time.

Default $5,000 profile:
- up to about $500 per position
- 5-second scans
- 20-second entry cooldown
- 0.4% stop
- 0.8% target
- 2-minute max hold
- 2% daily loss shutdown
- paper execution only

Fast crypto scalping is extremely sensitive to fees and slippage. This package
does not guarantee profits and contains no real-money Coinbase order adapter.
