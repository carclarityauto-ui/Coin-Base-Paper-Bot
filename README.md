# Multi-Crypto High-Risk Paper Scalper V4

Paper-only Railway bot for BTC-USD, ETH-USD, SOL-USD, and XRP-USD.

V4 is intentionally aggressive for paper testing. It uses up to 25% of paper equity per position, a low entry-score threshold, a 5-second entry cooldown, wider RSI/volatility gates, a 0.6% stop, a 3-minute maximum hold, and an 8% daily paper-loss shutdown. One position is open at a time.

The dashboard now reports the exact blockers for each market when an entry is rejected. This makes it possible to tune the strategy from observed paper results instead of simply forcing trades.

Exit targets remain cost-aware: the bot estimates round-trip fees, slippage and spread and requires a gross target intended to cover those costs plus the configured net-paper-profit target.

**Paper execution only. High risk does not guarantee profits.** Frequent scalping can lose money quickly after fees, slippage, adverse moves and false signals.
