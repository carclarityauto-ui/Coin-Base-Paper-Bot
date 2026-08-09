import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Railway Coinbase Paper Bot V2")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc


PRODUCT_ID = os.getenv("PRODUCT_ID", "BTC-USD")
STARTING_CASH = env_float("STARTING_CASH", 250.0)
MAX_POSITION_PCT = env_float("MAX_POSITION_PCT", 0.10)
CASH_RESERVE_PCT = env_float("CASH_RESERVE_PCT", 0.05)
FEE_PCT = env_float("FEE_PCT_PER_SIDE", 0.006)
SLIPPAGE_PCT = env_float("SLIPPAGE_PCT_PER_SIDE", 0.0003)
POLL_SECONDS = max(10, int(os.getenv("POLL_SECONDS", "15")))
MIN_EDGE_MULTIPLE = env_float("MIN_EDGE_MULTIPLE", 2.5)
ENTRY_SCORE = env_float("ENTRY_SCORE", 0.62)
EXIT_SCORE = env_float("EXIT_SCORE", -0.10)
DAILY_LOSS_LIMIT_PCT = env_float("DAILY_LOSS_LIMIT_PCT", 0.01)
STOP_LOSS_PCT = env_float("STOP_LOSS_PCT", 0.006)
TAKE_PROFIT_PCT = env_float("TAKE_PROFIT_PCT", 0.018)
DEFAULT_AUTO = os.getenv("AUTO_TRADING", "false").lower() == "true"
STATE_PATH = Path(os.getenv("DATA_PATH", "/data/state.json"))
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "paper").lower()

if EXECUTION_MODE != "paper":
    raise RuntimeError("This package is paper-only. EXECUTION_MODE must equal paper.")
if not (0 < MAX_POSITION_PCT <= 0.25):
    raise RuntimeError("MAX_POSITION_PCT must be a decimal fraction between 0 and 0.25.")
if not (0 <= CASH_RESERVE_PCT < 0.50):
    raise RuntimeError("CASH_RESERVE_PCT must be between 0 and 0.50.")
if not (0 <= FEE_PCT <= 0.05 and 0 <= SLIPPAGE_PCT <= 0.02):
    raise RuntimeError("Fee or slippage setting is outside the permitted safety range.")

client = httpx.AsyncClient(
    base_url="https://api.exchange.coinbase.com",
    timeout=15,
    headers={"User-Agent": "railway-coinbase-paper-bot-v2/1.0"},
)
state_lock = asyncio.Lock()
last_manual_action_at = 0.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def initial_state() -> dict[str, Any]:
    return {
        "version": 2,
        "product_id": PRODUCT_ID,
        "cash": STARTING_CASH,
        "equity": STARTING_CASH,
        "day_start_equity": STARTING_CASH,
        "day": today_utc(),
        "position": None,
        "last_price": None,
        "last_signal": None,
        "fees_paid": 0.0,
        "realized_pnl": 0.0,
        "daily_pnl": 0.0,
        "entries_today": 0,
        "trades": [],
        "updated_at": None,
        "auto_trading": DEFAULT_AUTO,
        "daily_lock": False,
        "last_error": None,
        "last_entry_time": None,
    }


def load_state() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            loaded = json.loads(STATE_PATH.read_text())
            if loaded.get("version") == 2:
                return loaded
    except Exception:
        pass
    return initial_state()


state = load_state()


def save_state() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2))
    temporary.replace(STATE_PATH)


def reset_day_if_needed() -> None:
    day = today_utc()
    if state.get("day") != day:
        state["day"] = day
        state["day_start_equity"] = float(state.get("equity", STARTING_CASH))
        state["daily_pnl"] = 0.0
        state["entries_today"] = 0
        state["daily_lock"] = False


async def get_market() -> dict[str, Any]:
    ticker_response, candle_response = await asyncio.gather(
        client.get(f"/products/{PRODUCT_ID}/ticker"),
        client.get(f"/products/{PRODUCT_ID}/candles", params={"granularity": 60}),
    )
    ticker_response.raise_for_status()
    candle_response.raise_for_status()
    ticker = ticker_response.json()
    frame = pd.DataFrame(
        candle_response.json(),
        columns=["time", "low", "high", "open", "close", "volume"],
    ).sort_values("time").tail(120).reset_index(drop=True)
    if len(frame) < 30:
        raise RuntimeError("Insufficient market data returned by Coinbase")
    return {
        "price": float(ticker["price"]),
        "bid": float(ticker["bid"]),
        "ask": float(ticker["ask"]),
        "candles": frame,
    }


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def analyze(market: dict[str, Any]) -> dict[str, Any]:
    frame = market["candles"]
    close = frame["close"].astype(float)
    volume = frame["volume"].astype(float)
    returns = close.pct_change()
    fast = close.ewm(span=8, adjust=False).mean()
    slow = close.ewm(span=21, adjust=False).mean()
    current_rsi = float(calculate_rsi(close).iloc[-1])
    trend = float((fast.iloc[-1] - slow.iloc[-1]) / close.iloc[-1])
    volatility = float(returns.tail(20).std() or 0)
    volume_ratio = float(volume.iloc[-1] / max(volume.tail(20).mean(), 1e-12))
    momentum = float(returns.tail(3).mean() or 0)
    spread = max(0.0, (market["ask"] - market["bid"]) / market["price"])
    clip = lambda value: float(np.clip(value, -1, 1))
    score = (
        0.40 * clip(trend / 0.002)
        + 0.30 * clip((current_rsi - 50) / 20)
        + 0.20 * clip((volume_ratio - 1) / 0.5)
        + 0.10 * clip(momentum / 0.001)
    )
    round_trip_cost = 2 * FEE_PCT + 2 * SLIPPAGE_PCT + spread
    estimated_move = max(volatility * 2, abs(trend) * 1.5)
    cost_gate = estimated_move >= round_trip_cost * MIN_EDGE_MULTIPLE
    return {
        "score": score,
        "rsi": current_rsi,
        "trend_pct": trend * 100,
        "volatility_pct": volatility * 100,
        "volume_ratio": volume_ratio,
        "spread_pct": spread * 100,
        "estimated_move_pct": estimated_move * 100,
        "round_trip_cost_pct": round_trip_cost * 100,
        "cost_gate": bool(cost_gate),
        "buy": bool(score >= ENTRY_SCORE and cost_gate and 52 <= current_rsi <= 72),
        "sell": bool(score <= EXIT_SCORE or current_rsi >= 78),
    }


def mark_equity(price: float) -> None:
    quantity = float(state["position"]["quantity"]) if state.get("position") else 0.0
    state["equity"] = float(state["cash"]) + quantity * price
    state["daily_pnl"] = state["equity"] - float(state["day_start_equity"])
    state["daily_lock"] = state["daily_pnl"] <= -float(state["day_start_equity"]) * DAILY_LOSS_LIMIT_PCT


def assert_invariants() -> None:
    cash = float(state["cash"])
    equity = float(state["equity"])
    if cash < -1e-8:
        raise RuntimeError("Safety invariant failed: cash cannot be negative")
    if not np.isfinite(cash) or not np.isfinite(equity):
        raise RuntimeError("Safety invariant failed: non-finite account value")
    position = state.get("position")
    if position:
        notional = float(position["quantity"]) * float(position["entry_price"])
        cap = max(float(position["entry_equity"]) * MAX_POSITION_PCT, 0)
        if notional > cap * 1.0001:
            raise RuntimeError("Safety invariant failed: position exceeds hard cap")


def paper_buy(price: float, reason: str) -> None:
    last_entry = state.get("last_entry_time")
    if last_entry:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_entry)).total_seconds()
        if elapsed < MIN_SECONDS_BETWEEN_ENTRIES:
            raise HTTPException(400, "Minimum entry cooldown is active.")
    reset_day_if_needed()
    if state.get("daily_lock"):
        raise HTTPException(400, "Daily loss lock is active.")
    if int(state.get("entries_today", 0)) >= MAX_TRADES_PER_DAY:
        raise HTTPException(400, "Maximum daily entry count reached.")
    if state.get("position"):
        raise HTTPException(400, "A paper position is already open.")

    equity = float(state["equity"])
    cash = float(state["cash"])
    reserve = equity * CASH_RESERVE_PCT
    spendable_cash = max(0.0, cash - reserve)
    maximum_notional = equity * MAX_POSITION_PCT
    # Fee-inclusive budget: notional + fee may not exceed spendable cash.
    notional = min(maximum_notional, spendable_cash / (1 + FEE_PCT))
    if notional < 1.0:
        raise HTTPException(400, "Available paper cash is too small for the configured safeguards.")

    fill = price * (1 + SLIPPAGE_PCT)
    fee = notional * FEE_PCT
    quantity = notional / fill
    total_debit = notional + fee
    if total_debit > cash + 1e-9:
        raise HTTPException(400, "Order rejected because total cost exceeds available paper cash.")

    original = json.loads(json.dumps(state))
    try:
        state["cash"] = cash - total_debit
        state["fees_paid"] = float(state["fees_paid"]) + fee
        state["position"] = {
            "quantity": quantity,
            "entry_price": fill,
            "entry_fee": fee,
            "entry_notional": notional,
            "entry_equity": equity,
            "entry_time": utc_now(),
        }
        state["entries_today"] = int(state.get("entries_today", 0)) + 1
        state["trades"].append({
            "time": utc_now(), "side": "BUY", "price": fill,
            "quantity": quantity, "notional": notional, "fee": fee,
            "pnl": 0.0, "reason": reason,
        })
        mark_equity(price)
        assert_invariants()
    except Exception:
        state.clear(); state.update(original)
        raise


def paper_sell(price: float, reason: str) -> None:
    if not state.get("position"):
        raise HTTPException(400, "There is no paper position to sell.")
    position = state["position"]
    fill = price * (1 - SLIPPAGE_PCT)
    proceeds = float(position["quantity"]) * fill
    fee = proceeds * FEE_PCT
    pnl = (
        (fill - float(position["entry_price"])) * float(position["quantity"])
        - float(position["entry_fee"]) - fee
    )
    original = json.loads(json.dumps(state))
    try:
        state["cash"] = float(state["cash"]) + proceeds - fee
        state["fees_paid"] = float(state["fees_paid"]) + fee
        state["realized_pnl"] = float(state["realized_pnl"]) + pnl
        state["position"] = None
        state["trades"].append({
            "time": utc_now(), "side": "SELL", "price": fill,
            "quantity": float(position["quantity"]), "notional": proceeds,
            "fee": fee, "pnl": pnl, "reason": reason,
        })
        mark_equity(price)
        assert_invariants()
    except Exception:
        state.clear(); state.update(original)
        raise


async def update_once() -> None:
    market = await get_market()
    signal = analyze(market)
    async with state_lock:
        reset_day_if_needed()
        state["last_price"] = market["price"]
        state["last_signal"] = signal
        state["updated_at"] = utc_now()
        state["last_error"] = None
        mark_equity(market["price"])
        if state.get("auto_trading"):
            if not state.get("position") and signal["buy"] and not state["daily_lock"]:
                try:
                    paper_buy(market["ask"], "automatic_signal")
                except HTTPException:
                    pass
            elif state.get("position"):
                entry = float(state["position"]["entry_price"])
                move = market["bid"] / entry - 1
                if move <= -STOP_LOSS_PCT:
                    paper_sell(market["bid"], "automatic_stop")
                elif move >= TAKE_PROFIT_PCT:
                    paper_sell(market["bid"], "automatic_target")
                elif signal["sell"]:
                    paper_sell(market["bid"], "automatic_signal_exit")
        mark_equity(market["price"])
        assert_invariants()
        save_state()


async def bot_loop() -> None:
    while True:
        try:
            await update_once()
        except Exception as error:
            async with state_lock:
                state["last_error"] = str(error)
                state["updated_at"] = utc_now()
                save_state()
        await asyncio.sleep(POLL_SECONDS)


@app.on_event("startup")
async def startup() -> None:
    asyncio.create_task(bot_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    await client.aclose()


@app.get("/")
async def index():
    return FileResponse("app/static/index.html")


@app.get("/health")
async def health():
    return {"ok": True, "version": 2, "mode": EXECUTION_MODE, "updated_at": state.get("updated_at")}


@app.get("/api/status")
async def status():
    async with state_lock:
        return state


def enforce_manual_cooldown() -> None:
    global last_manual_action_at
    now = time.monotonic()
    if now - last_manual_action_at < 5:
        raise HTTPException(429, "Please wait five seconds between manual order requests.")
    last_manual_action_at = now


@app.post("/api/buy")
async def buy(request: Request):
    enforce_manual_cooldown()
    market = await get_market()
    async with state_lock:
        paper_buy(market["ask"], "manual")
        mark_equity(market["price"])
        save_state()
        return state


@app.post("/api/sell")
async def sell(request: Request):
    enforce_manual_cooldown()
    market = await get_market()
    async with state_lock:
        paper_sell(market["bid"], "manual")
        mark_equity(market["price"])
        save_state()
        return state


@app.post("/api/auto/start")
async def auto_start():
    async with state_lock:
        state["auto_trading"] = True
        save_state()
        return state


@app.post("/api/auto/stop")
async def auto_stop():
    async with state_lock:
        state["auto_trading"] = False
        save_state()
        return state


@app.post("/api/reset")
async def reset():
    async with state_lock:
        state.clear()
        state.update(initial_state())
        save_state()
        return state
