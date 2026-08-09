# Railway Coinbase Paper Bot V4

A complete rebuild for a **$5,000 paper account** with **no fixed daily trade-count cap**.

## Trading profile

V4 is intentionally more aggressive than V2/V3 while still enforcing hard limits:

- $5,000 starting paper balance
- 20% maximum position size: up to about $1,000 initially
- 0.5% account-risk budget per trade
- No fixed daily trade-count maximum
- 60-second minimum interval between entries
- One open position maximum
- 2% daily loss shutdown: about $100 initially
- Four consecutive losses trigger a two-hour entry lock
- Fee, spread, slippage, volatility, trend, momentum, and volume filters
- Fixed stop, profit target, trailing stop, and signal-reversal exits
- Atomic state persistence on `/data`
- Paper execution only

Unlimited trades means there is no arbitrary count cap. It does **not** force the
bot to trade. Every entry must pass the signal, market-regime, cost, cooldown,
loss-lock, cash, and risk checks.

No strategy is guaranteed profitable or “win-proof.”

## Replace the existing Railway project

1. Download and unzip this package.
2. Upload all files inside the folder to the existing GitHub repository.
3. Commit directly to `main`.
4. Railway will redeploy automatically.
5. Replace all Railway variables using `.env.example`.
6. Keep the existing volume mounted at `/data`.
7. Deploy the variable changes.
8. Open the dashboard and confirm it says **V4**.
9. Click **Reset** to initialize the $5,000 account.
10. Keep Auto stopped and run one manual buy/sell sizing test.
11. Confirm the position is no more than about $1,000.
12. Then click Start Auto for paper testing.

## Real money

This package deliberately contains no Coinbase authenticated trading adapter.
A real-money version should be a separate service with restricted credentials,
order previews, authentication, notional limits, reconciliation, and a kill switch.
