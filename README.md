# Rapid Multi-Crypto Paper Scalper V2

Paper-only Railway bot for BTC-USD, ETH-USD, SOL-USD, and XRP-USD.

It scans all four markets every few seconds and ranks them using short-term momentum,
trend, volume, RSI, volatility, and spread. All four are eligible to trade; the bot
rotates into the strongest qualifying setup and keeps one position open at a time.

The V2 exit logic is cost-aware. Instead of assuming a fixed gross target is profit,
it estimates round-trip fees, slippage, and spread, then requires the exit target to
cover those costs plus a configurable net paper-profit target.

Default $5,000 paper profile:
- BTC-USD, ETH-USD, SOL-USD, XRP-USD
- up to about $500 per position
- 5-second scans
- 20-second entry cooldown
- 0.4% stop
- 0.25% desired net paper profit after estimated trading costs
- minimum 0.6% gross target; actual target rises when estimated costs are higher
- 2-minute max hold
- 2% daily loss shutdown
- paper execution only

Important Railway variables:
- `AUTO_TRADING=true` to let the paper bot trade automatically
- `PRODUCTS=BTC-USD,ETH-USD,SOL-USD,XRP-USD`
- `FEE_PCT_PER_SIDE=0.004` is only a simulation assumption; set it to the fee rate you want to model
- `SLIPPAGE_PCT_PER_SIDE=0.0003`
- `NET_PROFIT_TARGET_PCT=0.0025`
- `MIN_GROSS_TARGET_PCT=0.006`
- `STOP_LOSS_PCT=0.004`

Fast scalping is highly sensitive to fees, spread, slippage, latency, and market moves.
This package does not guarantee profits and contains no real-money Coinbase order adapter.
