"""Trend Rider strategy: the single source of truth for live trading AND backtests.

Rules (daily candles, decided on the close, filled near the next open):
  ENTER  when the close breaks above the highest close of the prior ENTRY_N days
         AND the close is above its SMA_N-day average (only buy in uptrends).
  EXIT   when the close falls below the lowest close of the prior EXIT_N days,
         OR below a trailing stop = highest close since entry - ATR_MULT x ATR(14).
         The trailing stop only ever moves up.

Why this design: it trades rarely (a few times per coin per year), so fees are a
small fraction of each trade, and it lets winning trends run while cutting losers.
Expect a win rate around 40-55% with average wins 2-3x the size of average losses.
"""
from dataclasses import dataclass, asdict
import math
import numpy as np
import pandas as pd


@dataclass
class Params:
    entry_n: int = 20
    exit_n: int = 10
    sma_n: int = 100
    atr_n: int = 14
    atr_mult: float = 3.0

    def warmup(self) -> int:
        return max(self.sma_n, self.entry_n, self.exit_n, self.atr_n) + 2

    def dict(self):
        return asdict(self)


def indicators(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    """df needs columns open, high, low, close (one row per CLOSED daily candle, oldest first)."""
    c, h, l = df["close"].astype(float), df["high"].astype(float), df["low"].astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    out = df.copy()
    out["atr"] = tr.rolling(p.atr_n).mean()
    out["breakout_level"] = c.shift(1).rolling(p.entry_n).max()
    out["exit_level"] = c.shift(1).rolling(p.exit_n).min()
    out["sma"] = c.rolling(p.sma_n).mean()
    return out


def entry_signal(row) -> bool:
    vals = [row["close"], row["breakout_level"], row["sma"], row["atr"]]
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals):
        return False
    return row["close"] > row["breakout_level"] and row["close"] > row["sma"]


def new_stop(prev_stop: float, peak_close: float, atr: float, p: Params) -> float:
    return max(prev_stop, peak_close - p.atr_mult * atr)


def exit_signal(row, stop: float) -> tuple[bool, str]:
    if row["close"] < stop:
        return True, "trailing_stop"
    if not math.isnan(row["exit_level"]) and row["close"] < row["exit_level"]:
        return True, "trend_exit"
    return False, ""


def backtest(df: pd.DataFrame, p: Params, fee_per_side: float, slippage_per_side: float,
             start_cash: float = 10000.0) -> dict:
    """Full-allocation backtest on one symbol. Orders decided on a close fill at the next open."""
    d = indicators(df, p).reset_index(drop=True)
    cost = fee_per_side
    cash, qty = start_cash, 0.0
    pending, stop, peak, entry_cost, entry_time = None, 0.0, 0.0, 0.0, None
    trades, curve, fees = [], [], 0.0
    for i in range(p.warmup(), len(d)):
        row = d.iloc[i]
        if pending == "buy":
            px = row["open"] * (1 + slippage_per_side)
            notional = cash / (1 + cost)
            f = notional * cost
            qty, cash, fees = notional / px, cash - notional - f, fees + f
            entry_cost, entry_time = notional + f, int(row["time"])
            prev = d.iloc[i - 1]
            peak = prev["close"]
            stop = prev["close"] - p.atr_mult * prev["atr"]
        elif pending and pending.startswith("sell"):
            px = row["open"] * (1 - slippage_per_side)
            proceeds = qty * px
            f = proceeds * cost
            cash, fees = cash + proceeds - f, fees + f
            trades.append({"entry_time": entry_time, "exit_time": int(row["time"]), "net_pnl": float(proceeds - f - entry_cost),
                           "return_pct": float((proceeds - f) / entry_cost * 100 - 100), "reason": pending[5:]})
            qty = 0.0
        pending = None
        curve.append((row["time"], cash + qty * row["close"]))
        if qty == 0:
            if entry_signal(row):
                pending = "buy"
        else:
            peak = max(peak, row["close"])
            stop = new_stop(stop, peak, row["atr"], p)
            hit, why = exit_signal(row, stop)
            if hit:
                pending = "sell:" + why
    if qty > 0:
        last = d.iloc[-1]
        proceeds = qty * last["close"] * (1 - cost)
        trades.append({"entry_time": entry_time, "exit_time": None, "net_pnl": float(proceeds - entry_cost),
                       "return_pct": float(proceeds / entry_cost * 100 - 100), "reason": "still_open"})
    if not curve:
        return {"error": "not enough data"}
    eq = pd.Series([v for _, v in curve])
    first_close = float(d.iloc[p.warmup()]["close"])
    last_close = float(d.iloc[-1]["close"])
    years = max((curve[-1][0] - curve[0][0]) / 86400 / 365.25, 1e-9)
    closes = d["close"].iloc[p.warmup():].astype(float)
    pnl = np.array([t["net_pnl"] for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    return {
        "days": len(curve),
        "start_cash": start_cash,
        "final_equity": float(eq.iloc[-1]),
        "total_return_pct": float(eq.iloc[-1] / start_cash * 100 - 100),
        "cagr_pct": float(((eq.iloc[-1] / start_cash) ** (1 / years) - 1) * 100),
        "max_drawdown_pct": float((eq / eq.cummax() - 1).min() * 100),
        "trades": len(trades),
        "win_rate_pct": float(len(wins) / len(pnl) * 100) if len(pnl) else 0.0,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
        "fees_paid": float(fees),
        "buy_hold_return_pct": float(last_close / first_close * 100 - 100),
        "buy_hold_max_drawdown_pct": float((closes / closes.cummax() - 1).min() * 100),
        "recent_trades": trades[-8:],
    }
