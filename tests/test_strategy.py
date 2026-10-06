"""Run with:  python tests/test_strategy.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from app.strategy import Params, backtest, entry_signal, indicators, new_stop

def frame(closes):
    c = np.array(closes, float)
    return pd.DataFrame({"time": np.arange(len(c)) * 86400, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c})

p = Params()
# 1. Steady downtrend: never buys.
r = backtest(frame(np.linspace(200, 100, 300)), p, 0.006, 0.0005)
assert r["trades"] == 0 and abs(r["final_equity"] - 10000) < 1e-6, r

# 2. Strong uptrend: buys, makes money after fees.
r = backtest(frame(np.linspace(100, 300, 300)), p, 0.006, 0.0005)
assert r["trades"] >= 1 and r["final_equity"] > 10000, r

# 3. Up then crash: exits and keeps most gains (does not ride the whole crash).
up_down = list(np.linspace(100, 300, 250)) + list(np.linspace(300, 120, 100))
r = backtest(frame(up_down), p, 0.006, 0.0005)
assert r["final_equity"] > 10000 * 1.5, r
assert r["max_drawdown_pct"] > -35, r

# 4. Fees are charged: higher fees mean less money.
lo = backtest(frame(up_down), p, 0.001, 0)["final_equity"]
hi = backtest(frame(up_down), p, 0.01, 0)["final_equity"]
assert hi < lo

# 5. Trailing stop never moves down.
assert new_stop(100, 90, 5, p) == 100

# 6. No signal before enough history.
d = indicators(frame(np.linspace(100, 300, 50)), p)
assert not entry_signal(d.iloc[-1])

# 7. Wins are counted after fees: a tiny gross gain smaller than fees is a loss.
flat = list(np.linspace(100, 300, 150)) + [300.5] * 5 + list(np.linspace(300, 250, 30))
r = backtest(frame(flat), p, 0.05, 0)
assert all((t["net_pnl"] > 0) == (t["return_pct"] > 0) for t in r["recent_trades"])
print("All strategy tests passed.")
