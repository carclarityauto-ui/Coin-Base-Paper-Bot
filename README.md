# Multi-Market Paper Lab V6

Paper-only Railway trading research bot.

Markets:
- Crypto: BTC-USD, ETH-USD, SOL-USD, XRP-USD
- Stocks/ETFs: SPY, QQQ, AAPL, NVDA
- Futures proxies: ES=F, NQ=F, YM=F, RTY=F

V6 fixes the reset/dashboard mismatch from V5. Reset rebuilds clean paper sleeves, and starting cash accepts either `STARTING_CASH_PER_MARKET` or the older `STARTING_CASH` Railway variable.

This is simulation software. It does not place real-money orders and does not guarantee profit.
