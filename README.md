# Railway Coinbase Paper Bot V3

V3 defaults to a $5,000 paper account and removes the daily trade-count cap.

Safety controls retained:
- 10% maximum position size (about $500 initially)
- 1% daily account loss lock (about $50 initially)
- one open position maximum
- 60-second minimum interval between entries
- fee/spread/slippage-aware cost gate
- hard cash and notional validation
- paper execution only

Unlimited trade count does not force trades. Entries must still pass the strategy
and cost filters.

After deploying, apply the variables from `.env.example`, deploy, and click Reset
on the dashboard to initialize the $5,000 paper balance.

# Railway Coinbase Paper Bot V2

This corrected version fixes the oversized-position bug and adds server-side safety checks.
It uses live public Coinbase market data and simulated money only.

## Critical change

`MAX_POSITION_PCT` is a decimal fraction. Use `0.10` for 10%, not `10`.
The service refuses to start if this value exceeds `0.25`.

## Railway variables

Paste these into Railway Raw Editor:

```text
PRODUCT_ID=BTC-USD
STARTING_CASH=250
MAX_POSITION_PCT=0.10
CASH_RESERVE_PCT=0.05
FEE_PCT_PER_SIDE=0.006
SLIPPAGE_PCT_PER_SIDE=0.0003
POLL_SECONDS=15
MIN_EDGE_MULTIPLE=2.5
ENTRY_SCORE=0.62
EXIT_SCORE=-0.10
DAILY_LOSS_LIMIT_PCT=0.01
MAX_TRADES_PER_DAY=8
STOP_LOSS_PCT=0.006
TAKE_PROFIT_PCT=0.018
AUTO_TRADING=false
DATA_PATH=/data/state.json
EXECUTION_MODE=paper
```

## Safety controls

- Maximum position is capped by account equity and available cash.
- Fees are included before quantity is calculated.
- A cash reserve is retained.
- Negative cash is impossible; the order is rejected if invariants fail.
- Only one position can exist.
- Duplicate manual requests are blocked for 5 seconds.
- Daily loss and daily trade limits are enforced server-side.
- State is written atomically to `/data/state.json`.
- `EXECUTION_MODE` must equal `paper`; any other value is rejected.

## Upgrade from the first version

Upload these files over the existing GitHub repository, commit the changes, and Railway will redeploy.
Then update the Railway variables to the values above and press Reset on the dashboard.

## Real-money transition

Do not convert this deployment to real money by changing one variable. Use the same signal module only after:

1. A meaningful paper-test sample across different conditions.
2. Fee and fill reconciliation.
3. A separate Coinbase execution adapter with order previews and status reconciliation.
4. Restricted Coinbase API permissions and secure secrets.
5. Independent review of position sizing, duplicate-order controls, and emergency shutdown.

Coinbase real execution is intentionally not included in this package.
