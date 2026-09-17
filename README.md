# Multi-Market Paper Lab V5

Paper-only comparison framework for crypto, liquid U.S. stocks/ETFs, and index-futures price series.

Default universes:
- Crypto: BTC-USD, ETH-USD, SOL-USD, XRP-USD (Coinbase public market data)
- Stocks/ETFs: SPY, QQQ, AAPL, NVDA (public Yahoo chart data)
- Futures proxies: ES=F, NQ=F, YM=F, RTY=F (public Yahoo chart data)

Each market gets a separate $5,000 paper sleeve so results are directly comparable. The futures sleeve is a normalized price-return simulation, not exchange-accurate contract/margin accounting.

V5 is cost-aware: entries require estimated movement to exceed modeled round-trip costs by a safety margin. It records gross P/L, fees/costs, net P/L, win/loss counts, drawdown, blockers, and recent trades for each market.

This is research software, not a profit guarantee, and it contains no real-money broker/order adapter.
