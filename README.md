# Trend Rider Paper Bot V7

Paper-trading crypto bot (BTC, ETH, SOL, XRP on Coinbase prices). **No real orders are ever placed. No profit guarantee.**

## Strategy (daily candles)
- **Buy** when a coin closes above its highest close of the last 20 days **and** above its 100-day average.
- **Sell** when it closes below its lowest close of the last 10 days, or below a trailing stop
  (highest close since entry minus 3 x the 14-day average true range). The stop only moves up.
- Each coin gets up to 1/4 of the account. Wins and losses are counted **after fees**.
- New entries pause if the account falls 40% from its peak (exits still run).

It trades a few times per coin per year. Expect win rates around 40-55%, with average wins 2-3x average losses.

## Why V6.1 was replaced
Replaying V6.1's exact rules on two years of 5-minute BTC data (Oct 2024 - Oct 2026): about 1,770 trades,
gross profit about $17, fees about $1,840. The signals had no edge; fees caused the losses.

## Backtest (BTC, these exact rules, 0.6% fee + 0.05% slippage per side)
| Period | Strategy | Max drawdown | Buy & hold | B&H drawdown |
|---|---|---|---|---|
| 2021-01 to 2026-10 | +101% | -49% | +191% | -77% |
| 2024-10 to 2026-10 | +40% | -18% | +41% | -53% |

Across 81 parameter variations (2021-2026, same fees), 83% were profitable. The default settings were chosen in
advance, not tuned for the best result. The dashboard re-runs this backtest daily for every coin on Coinbase data.

## Endpoints
- `GET /api/status` - account, signals, trades, backtests
- `GET /api/backtest?symbol=ETH-USD&days=1500&fee_pct=0.6` - backtest one coin
- `POST /api/auto/start`, `/api/auto/stop`, `/api/reset`, `/api/backtest/refresh`

## Settings (Railway environment variables, all optional)
`SYMBOLS`, `STARTING_CASH` (5000), `FEE_PCT_PER_SIDE` (0.006), `SLIPPAGE_PCT_PER_SIDE` (0.0005),
`MAX_DRAWDOWN_HALT_PCT` (0.40), `ENTRY_LOOKBACK_DAYS` (20), `EXIT_LOOKBACK_DAYS` (10), `TREND_SMA_DAYS` (100),
`ATR_DAYS` (14), `ATR_STOP_MULT` (3.0), `CHECK_SECONDS` (300), `DATA_PATH` (/data/trend-rider-v7.json).

Attach a Railway volume at `/data` so the paper account survives redeploys.

## Tests
`python tests/test_strategy.py`
