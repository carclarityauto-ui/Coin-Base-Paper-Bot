"""Trend Rider Paper Bot V7 - paper trading only, no real orders are ever placed."""
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse

from app.strategy import Params, backtest, entry_signal, exit_signal, indicators, new_stop

VERSION = "Trend Rider Paper Bot V7"


def env_float(name, default):
    try:
        v = float(os.getenv(name, default))
        return v if v >= 0 else float(default)
    except (TypeError, ValueError):
        return float(default)


def env_int(name, default):
    try:
        return max(1, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return int(default)


SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS", "BTC-USD,ETH-USD,SOL-USD,XRP-USD").split(",") if s.strip()]
START_CASH = env_float("STARTING_CASH", 5000)
FEE = env_float("FEE_PCT_PER_SIDE", 0.006)          # Coinbase entry-tier taker fees are high; be conservative
SLIP = env_float("SLIPPAGE_PCT_PER_SIDE", 0.0005)
HALT_DD = env_float("MAX_DRAWDOWN_HALT_PCT", 0.40)  # stop opening NEW trades if account falls this far from its peak
BACKTEST_DAYS = env_int("BACKTEST_DAYS", 1825)
CHECK_SECONDS = env_int("CHECK_SECONDS", 300)
AUTO_DEFAULT = os.getenv("AUTO_TRADING", "true").lower() == "true"
PATH = Path(os.getenv("DATA_PATH", "/data/trend-rider-v7.json"))
PARAMS = Params(
    entry_n=env_int("ENTRY_LOOKBACK_DAYS", 20),
    exit_n=env_int("EXIT_LOOKBACK_DAYS", 10),
    sma_n=env_int("TREND_SMA_DAYS", 100),
    atr_n=env_int("ATR_DAYS", 14),
    atr_mult=env_float("ATR_STOP_MULT", 3.0),
)
CB = "https://api.exchange.coinbase.com"
DAY = 86400

lock = asyncio.Lock()
client: httpx.AsyncClient | None = None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def fresh_state():
    return {
        "version": VERSION, "auto_trading": AUTO_DEFAULT, "updated_at": None, "last_error": None,
        "settings": {"symbols": SYMBOLS, "fee_pct_per_side": FEE * 100, "slippage_pct_per_side": SLIP * 100,
                     "max_drawdown_halt_pct": HALT_DD * 100, **PARAMS.dict()},
        "account": {"cash": START_CASH, "equity": START_CASH, "start_cash": START_CASH, "peak": START_CASH,
                    "max_drawdown_pct": 0.0, "realized_pnl": 0.0, "gross_pnl": 0.0, "fees": 0.0,
                    "wins": 0, "losses": 0, "halted": False},
        "markets": {s: {"price": None, "last_candle": None, "position": None, "signal": None} for s in SYMBOLS},
        "trades": [], "backtests": {}, "backtest_updated": None,
    }


def load_state():
    try:
        if PATH.exists():
            old = json.loads(PATH.read_text())
            if old.get("version") == VERSION:
                s = fresh_state()
                s.update({k: v for k, v in old.items() if k not in ("settings", "markets")})
                for sym in SYMBOLS:
                    if sym in old.get("markets", {}):
                        s["markets"][sym].update(old["markets"][sym])
                return s
    except Exception:
        pass
    return fresh_state()


state = load_state()


def save():
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(PATH)


# ---------------- market data ----------------
async def candles(symbol, days):
    """Closed daily candles, oldest first. Coinbase returns at most 300 per request."""
    end = int(time.time()) // DAY * DAY  # start of today UTC: excludes today's unfinished candle
    start = end - days * DAY
    rows, cur = [], start
    while cur < end:
        stop = min(cur + 299 * DAY, end)
        r = await client.get(f"{CB}/products/{symbol}/candles", params={
            "granularity": DAY,
            "start": datetime.fromtimestamp(cur, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(stop, timezone.utc).isoformat()})
        r.raise_for_status()
        rows += r.json()
        cur = stop
        await asyncio.sleep(0.25)  # stay well under public rate limits
    df = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
    df = df.drop_duplicates("time").sort_values("time")
    df = df[df["time"] < end].reset_index(drop=True)
    return df


async def ticker(symbol):
    r = await client.get(f"{CB}/products/{symbol}/ticker")
    r.raise_for_status()
    return float(r.json()["price"])


# ---------------- paper account ----------------
def mark_to_market():
    a = state["account"]
    held = sum(m["position"]["quantity"] * (m["price"] or m["position"]["entry_price"])
               for m in state["markets"].values() if m["position"])
    a["equity"] = a["cash"] + held
    a["peak"] = max(a["peak"], a["equity"])
    dd = (a["peak"] - a["equity"]) / a["peak"] * 100 if a["peak"] else 0
    a["max_drawdown_pct"] = max(a["max_drawdown_pct"], dd)
    a["current_drawdown_pct"] = dd
    a["halted"] = dd >= HALT_DD * 100


def paper_buy(sym, price, row):
    a, m = state["account"], state["markets"][sym]
    open_slots = len(SYMBOLS) - sum(1 for x in state["markets"].values() if x["position"])
    budget = min(a["equity"] / len(SYMBOLS), a["cash"] / max(open_slots, 1))
    notional = budget / (1 + FEE)
    if notional < 10:
        return
    fill = price * (1 + SLIP)
    fee = notional * FEE
    a["cash"] -= notional + fee
    a["fees"] += fee
    stop = row["close"] - PARAMS.atr_mult * row["atr"]
    m["position"] = {"quantity": notional / fill, "entry_price": fill, "cost_basis": notional + fee,
                     "entry_time": now_iso(), "peak_close": float(row["close"]), "stop": float(stop)}
    state["trades"].append({"time": now_iso(), "symbol": sym, "side": "BUY", "price": fill, "notional": notional,
                            "fee": fee, "net_pnl": None, "reason": "breakout_in_uptrend"})


def paper_sell(sym, price, reason):
    a, m = state["account"], state["markets"][sym]
    p = m["position"]
    fill = price * (1 - SLIP)
    proceeds = p["quantity"] * fill
    fee = proceeds * FEE
    net = proceeds - fee - p["cost_basis"]          # profit AFTER both fees: this decides win/loss
    a["cash"] += proceeds - fee
    a["fees"] += fee
    a["realized_pnl"] += net
    a["gross_pnl"] += (fill - p["entry_price"]) * p["quantity"]
    a["wins" if net > 0 else "losses"] += 1
    state["trades"].append({"time": now_iso(), "symbol": sym, "side": "SELL", "price": fill, "notional": proceeds,
                            "fee": fee, "net_pnl": net, "return_pct": net / p["cost_basis"] * 100, "reason": reason})
    state["trades"] = state["trades"][-500:]
    m["position"] = None


# ---------------- trading loop ----------------
async def process(sym):
    df = await candles(sym, PARAMS.warmup() + 40)
    price = await ticker(sym)
    d = indicators(df, PARAMS)
    row = d.iloc[-1]
    async with lock:
        m = state["markets"][sym]
        m["price"] = price
        pos = m["position"]
        m["signal"] = {
            "last_close": float(row["close"]), "breakout_level": float(row["breakout_level"]),
            "trend_sma": float(row["sma"]), "exit_level": float(row["exit_level"]), "atr": float(row["atr"]),
            "uptrend": bool(row["close"] > row["sma"]),
            "status": ("HOLDING" if pos else "BUY SIGNAL" if entry_signal(row) else
                       "WAITING: below trend average" if row["close"] <= row["sma"] else
                       "WAITING: no breakout yet"),
        }
        candle_id = int(row["time"])
        if m["last_candle"] == candle_id:
            return  # already acted on this day's close
        m["last_candle"] = candle_id
        if not state["auto_trading"]:
            return
        mark_to_market()
        if pos:
            pos["peak_close"] = max(pos["peak_close"], float(row["close"]))
            pos["stop"] = float(new_stop(pos["stop"], pos["peak_close"], float(row["atr"]), PARAMS))
            hit, why = exit_signal(row, pos["stop"])
            if hit:
                paper_sell(sym, price, why)
        elif entry_signal(row) and not state["account"]["halted"]:
            paper_buy(sym, price, row)


async def run_backtests():
    results = {}
    for sym in SYMBOLS:
        try:
            df = await candles(sym, BACKTEST_DAYS)
            results[sym] = backtest(df, PARAMS, FEE, SLIP)
            results[sym]["from"] = datetime.fromtimestamp(int(df["time"].iloc[0]), timezone.utc).date().isoformat()
        except Exception as e:
            results[sym] = {"error": str(e)}
    async with lock:
        state["backtests"] = results
        state["backtest_updated"] = now_iso()
        save()


async def loop():
    while True:
        errors = []
        for sym in SYMBOLS:
            try:
                await process(sym)
            except Exception as e:
                errors.append(f"{sym}: {e}")
        async with lock:
            mark_to_market()
            state["updated_at"] = now_iso()
            state["last_error"] = "; ".join(errors) or None
            save()
        bt = state.get("backtest_updated")
        if not bt or (datetime.now(timezone.utc) - datetime.fromisoformat(bt)).total_seconds() > DAY:
            await run_backtests()
        await asyncio.sleep(CHECK_SECONDS)


@asynccontextmanager
async def lifespan(_app):
    global client
    client = httpx.AsyncClient(timeout=20, headers={"User-Agent": "TrendRiderPaperBot/7"})
    task = asyncio.create_task(loop())
    yield
    task.cancel()
    await client.aclose()


app = FastAPI(title=VERSION, lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse("app/static/index.html")


@app.get("/health")
async def health():
    return {"ok": True, "version": VERSION, "paper_only": True}


@app.get("/api/status")
async def status():
    async with lock:
        return state


@app.post("/api/auto/start")
async def start():
    async with lock:
        state["auto_trading"] = True
        save()
        return state


@app.post("/api/auto/stop")
async def stop():
    async with lock:
        state["auto_trading"] = False
        save()
        return state


@app.post("/api/reset")
async def reset():
    async with lock:
        keep_bt, keep_time = state.get("backtests", {}), state.get("backtest_updated")
        state.clear()
        state.update(fresh_state())
        state["backtests"], state["backtest_updated"] = keep_bt, keep_time
        save()
        return state


@app.post("/api/backtest/refresh")
async def refresh_backtests():
    await run_backtests()
    return state["backtests"]


@app.get("/api/backtest")
async def backtest_one(symbol: str = Query("BTC-USD"), days: int = Query(1825, ge=200, le=4000),
                       fee_pct: float = Query(None, ge=0, le=2)):
    df = await candles(symbol.upper(), days)
    fee = FEE if fee_pct is None else fee_pct / 100
    return {"symbol": symbol.upper(), "fee_pct_per_side": fee * 100, **backtest(df, PARAMS, fee, SLIP)}
