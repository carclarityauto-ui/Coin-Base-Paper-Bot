import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

VERSION = 4
app = FastAPI(title="Railway Coinbase Paper Bot V4")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


PRODUCT_ID = os.getenv("PRODUCT_ID", "BTC-USD")
STARTING_CASH = env_float("STARTING_CASH", 5000.0)
MAX_POSITION_PCT = env_float("MAX_POSITION_PCT", 0.20)
RISK_PER_TRADE_PCT = env_float("RISK_PER_TRADE_PCT", 0.005)
CASH_RESERVE_PCT = env_float("CASH_RESERVE_PCT", 0.05)

FEE_PCT = env_float("FEE_PCT_PER_SIDE", 0.006)
SLIPPAGE_PCT = env_float("SLIPPAGE_PCT_PER_SIDE", 0.0003)

POLL_SECONDS = max(10, env_int("POLL_SECONDS", 15))
MIN_SECONDS_BETWEEN_ENTRIES = max(30, env_int("MIN_SECONDS_BETWEEN_ENTRIES", 60))
MIN_EDGE_MULTIPLE = env_float("MIN_EDGE_MULTIPLE", 2.0)

ENTRY_SCORE = env_float("ENTRY_SCORE", 0.58)
EXIT_SCORE = env_float("EXIT_SCORE", -0.08)
STOP_LOSS_PCT = env_float("STOP_LOSS_PCT", 0.0075)
TAKE_PROFIT_PCT = env_float("TAKE_PROFIT_PCT", 0.0225)
TRAILING_STOP_PCT = env_float("TRAILING_STOP_PCT", 0.009)

DAILY_LOSS_LIMIT_PCT = env_float("DAILY_LOSS_LIMIT_PCT", 0.02)
LOSS_STREAK_LIMIT = env_int("LOSS_STREAK_LIMIT", 4)
LOSS_LOCK_MINUTES = env_int("LOSS_LOCK_MINUTES", 120)

DEFAULT_AUTO = os.getenv("AUTO_TRADING", "false").lower() == "true"
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "paper").lower()
STATE_PATH = Path(os.getenv("DATA_PATH", "/data/state.json"))

if EXECUTION_MODE != "paper":
    raise RuntimeError("V4 supports paper execution only.")
if not 100 <= STARTING_CASH <= 10_000_000:
    raise RuntimeError("STARTING_CASH is outside the permitted range.")
if not 0.01 <= MAX_POSITION_PCT <= 0.25:
    raise RuntimeError("MAX_POSITION_PCT must be between 1% and 25%.")
if not 0.0005 <= RISK_PER_TRADE_PCT <= 0.01:
    raise RuntimeError("RISK_PER_TRADE_PCT must be between 0.05% and 1%.")
if not 0.0 <= CASH_RESERVE_PCT <= 0.50:
    raise RuntimeError("CASH_RESERVE_PCT is outside the permitted range.")
if not 0 <= FEE_PCT <= 0.02 or not 0 <= SLIPPAGE_PCT <= 0.01:
    raise RuntimeError("Fee or slippage setting is outside the permitted safety range.")
if not 0.002 <= STOP_LOSS_PCT <= 0.03:
    raise RuntimeError("STOP_LOSS_PCT is outside the permitted range.")
if not STOP_LOSS_PCT < TAKE_PROFIT_PCT <= 0.10:
    raise RuntimeError("TAKE_PROFIT_PCT must exceed STOP_LOSS_PCT and be <= 10%.")
if not 0.005 <= TRAILING_STOP_PCT <= 0.05:
    raise RuntimeError("TRAILING_STOP_PCT is outside the permitted range.")
if not 0.005 <= DAILY_LOSS_LIMIT_PCT <= 0.05:
    raise RuntimeError("DAILY_LOSS_LIMIT_PCT is outside the permitted range.")

client = httpx.AsyncClient(
    base_url="https://api.exchange.coinbase.com",
    timeout=15.0,
    headers={"User-Agent": "railway-coinbase-paper-bot-v4/1.0"},
)
state_lock = asyncio.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def initial_state() -> dict[str, Any]:
    return {
        "version": VERSION,
        "product_id": PRODUCT_ID,
        "cash": STARTING_CASH,
        "equity": STARTING_CASH,
        "day_start_equity": STARTING_CASH,
        "day": utc_day(),
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
        "loss_streak": 0,
        "loss_lock_until": None,
        "last_entry_time": None,
        "last_action_at": None,
        "last_error": None,
    }


def normalize_loaded_state(loaded: dict[str, Any]) -> dict[str, Any]:
    base = initial_state()
    base.update(loaded)
    base["version"] = VERSION
    base["product_id"] = PRODUCT_ID
    base["auto_trading"] = bool(base.get("auto_trading", DEFAULT_AUTO))
    return base


def load_state() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return normalize_loaded_state(json.loads(STATE_PATH.read_text()))
    except Exception:
        pass
    return initial_state()


state = load_state()


def save_state() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = STATE_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, indent=2))
    temp_path.replace(STATE_PATH)


def reset_day_if_needed() -> None:
    if state.get("day") != utc_day():
        state["day"] = utc_day()
        state["day_start_equity"] = float(state.get("equity", STARTING_CASH))
        state["daily_pnl"] = 0.0
        state["entries_today"] = 0
        state["daily_lock"] = False


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    relative_strength = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + relative_strength)).fillna(50)


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
    ).sort_values("time").tail(180).reset_index(drop=True)

    if len(frame) < 60:
        raise RuntimeError("Insufficient Coinbase candle history.")

    return {
        "price": float(ticker["price"]),
        "bid": float(ticker["bid"]),
        "ask": float(ticker["ask"]),
        "candles": frame,
    }


def analyze(market: dict[str, Any]) -> dict[str, Any]:
    frame = market["candles"]
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    volume = frame["volume"].astype(float)
    returns = close.pct_change()

    ema_fast = close.ewm(span=8, adjust=False).mean()
    ema_slow = close.ewm(span=21, adjust=False).mean()
    ema_regime = close.ewm(span=55, adjust=False).mean()
    current_rsi = float(calculate_rsi(close).iloc[-1])
    trend = float((ema_fast.iloc[-1] - ema_slow.iloc[-1]) / close.iloc[-1])
    regime = float((ema_slow.iloc[-1] - ema_regime.iloc[-1]) / close.iloc[-1])
    volatility = float(returns.tail(20).std() or 0)
    volume_ratio = float(volume.iloc[-1] / max(volume.tail(20).mean(), 1e-12))
    momentum = float(returns.tail(4).mean() or 0)
    spread = max(0.0, (market["ask"] - market["bid"]) / market["price"])

    recent_high = float(high.tail(20).max())
    recent_low = float(low.tail(20).min())
    midpoint = (recent_high + recent_low) / 2
    breakout = (float(close.iloc[-1]) - midpoint) / max(float(close.iloc[-1]), 1e-12)

    clip = lambda value: float(np.clip(value, -1, 1))
    score = (
        0.30 * clip(trend / 0.0015)
        + 0.20 * clip(regime / 0.0020)
        + 0.20 * clip((current_rsi - 50) / 18)
        + 0.15 * clip((volume_ratio - 1) / 0.45)
        + 0.10 * clip(momentum / 0.001)
        + 0.05 * clip(breakout / 0.002)
    )

    round_trip_cost = 2 * FEE_PCT + 2 * SLIPPAGE_PCT + spread
    estimated_move = max(volatility * 2.2, abs(trend) * 1.8, abs(momentum) * 2.0)
    cost_gate = estimated_move >= round_trip_cost * MIN_EDGE_MULTIPLE
    volatility_gate = 0.0005 <= volatility <= 0.025
    trend_gate = regime > -0.0015

    return {
        "score": score,
        "rsi": current_rsi,
        "trend_pct": trend * 100,
        "regime_pct": regime * 100,
        "volatility_pct": volatility * 100,
        "volume_ratio": volume_ratio,
        "momentum_pct": momentum * 100,
        "spread_pct": spread * 100,
        "estimated_move_pct": estimated_move * 100,
        "round_trip_cost_pct": round_trip_cost * 100,
        "cost_gate": bool(cost_gate),
        "volatility_gate": bool(volatility_gate),
        "trend_gate": bool(trend_gate),
        "buy": bool(
            score >= ENTRY_SCORE
            and cost_gate
            and volatility_gate
            and trend_gate
            and 50 <= current_rsi <= 76
        ),
        "sell": bool(score <= EXIT_SCORE or current_rsi >= 82),
    }


def mark_equity(price: float) -> None:
    position = state.get("position")
    quantity = float(position["quantity"]) if position else 0.0
    state["equity"] = float(state["cash"]) + quantity * price
    state["daily_pnl"] = state["equity"] - float(state["day_start_equity"])
    state["daily_lock"] = (
        state["daily_pnl"]
        <= -float(state["day_start_equity"]) * DAILY_LOSS_LIMIT_PCT
    )


def entry_lock_active() -> bool:
    lock_until = state.get("loss_lock_until")
    if not lock_until:
        return False
    return datetime.fromisoformat(lock_until) > datetime.now(timezone.utc)


def assert_invariants() -> None:
    cash = float(state["cash"])
    equity = float(state["equity"])
    if cash < -1e-7:
        raise RuntimeError("Safety invariant failed: cash became negative.")
    if equity < -1e-7:
        raise RuntimeError("Safety invariant failed: equity became negative.")

    position = state.get("position")
    if position:
        notional = float(position["quantity"]) * float(position["entry_price"])
        cap = float(position["entry_equity"]) * MAX_POSITION_PCT
        if notional > cap * 1.0001:
            raise RuntimeError("Safety invariant failed: position exceeds hard cap.")


def paper_buy(price: float, reason: str) -> None:
    reset_day_if_needed()

    if state.get("daily_lock"):
        raise HTTPException(400, "Daily loss lock is active.")
    if entry_lock_active():
        raise HTTPException(400, "Loss-streak lock is active.")
    if state.get("position"):
        raise HTTPException(400, "A paper position is already open.")

    last_entry = state.get("last_entry_time")
    if last_entry:
        elapsed = (
            datetime.now(timezone.utc) - datetime.fromisoformat(last_entry)
        ).total_seconds()
        if elapsed < MIN_SECONDS_BETWEEN_ENTRIES:
            raise HTTPException(400, "Minimum entry cooldown is active.")

    equity = float(state["equity"])
    cash = float(state["cash"])
    reserve = equity * CASH_RESERVE_PCT
    spendable_cash = max(0.0, cash - reserve)

    max_by_position = equity * MAX_POSITION_PCT
    risk_budget = equity * RISK_PER_TRADE_PCT
    max_by_risk = risk_budget / STOP_LOSS_PCT
    notional = min(max_by_position, max_by_risk, spendable_cash / (1 + FEE_PCT))

    if notional < 10:
        raise HTTPException(400, "Available paper cash is too small.")

    fill = price * (1 + SLIPPAGE_PCT)
    fee = notional * FEE_PCT
    quantity = notional / fill
    total_debit = notional + fee

    if total_debit > cash + 1e-9:
        raise HTTPException(400, "Order cost exceeds available paper cash.")

    snapshot = json.loads(json.dumps(state))
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
            "highest_price": fill,
        }
        state["entries_today"] = int(state.get("entries_today", 0)) + 1
        state["last_entry_time"] = utc_now()
        state["last_action_at"] = utc_now()
        state["trades"].append({
            "time": utc_now(),
            "side": "BUY",
            "notional": notional,
            "price": fill,
            "quantity": quantity,
            "fee": fee,
            "pnl": 0.0,
            "reason": reason,
        })
        mark_equity(price)
        assert_invariants()
    except Exception:
        state.clear()
        state.update(snapshot)
        raise


def paper_sell(price: float, reason: str) -> None:
    position = state.get("position")
    if not position:
        raise HTTPException(400, "There is no paper position to sell.")

    fill = price * (1 - SLIPPAGE_PCT)
    proceeds = float(position["quantity"]) * fill
    fee = proceeds * FEE_PCT
    pnl = (
        (fill - float(position["entry_price"])) * float(position["quantity"])
        - float(position["entry_fee"])
        - fee
    )

    snapshot = json.loads(json.dumps(state))
    try:
        state["cash"] = float(state["cash"]) + proceeds - fee
        state["fees_paid"] = float(state["fees_paid"]) + fee
        state["realized_pnl"] = float(state["realized_pnl"]) + pnl
        state["position"] = None
        state["last_action_at"] = utc_now()

        if pnl < 0:
            state["loss_streak"] = int(state.get("loss_streak", 0)) + 1
            if state["loss_streak"] >= LOSS_STREAK_LIMIT:
                state["loss_lock_until"] = (
                    datetime.now(timezone.utc)
                    + timedelta(minutes=LOSS_LOCK_MINUTES)
                ).isoformat()
        else:
            state["loss_streak"] = 0
            state["loss_lock_until"] = None

        state["trades"].append({
            "time": utc_now(),
            "side": "SELL",
            "notional": proceeds,
            "price": fill,
            "quantity": float(position["quantity"]),
            "fee": fee,
            "pnl": pnl,
            "reason": reason,
        })
        mark_equity(price)
        assert_invariants()
    except Exception:
        state.clear()
        state.update(snapshot)
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

        position = state.get("position")
        if position:
            position["highest_price"] = max(
                float(position.get("highest_price", position["entry_price"])),
                market["bid"],
            )

        mark_equity(market["price"])

        if state.get("auto_trading"):
            if not state.get("position"):
                if signal["buy"] and not state["daily_lock"] and not entry_lock_active():
                    try:
                        paper_buy(market["ask"], "automatic_signal")
                    except HTTPException:
                        pass
            else:
                position = state["position"]
                entry_price = float(position["entry_price"])
                move = market["bid"] / entry_price - 1
                trailing_floor = (
                    float(position["highest_price"]) * (1 - TRAILING_STOP_PCT)
                )

                if move <= -STOP_LOSS_PCT:
                    paper_sell(market["bid"], "automatic_stop")
                elif move >= TAKE_PROFIT_PCT:
                    paper_sell(market["bid"], "automatic_target")
                elif market["bid"] <= trailing_floor and move > 0:
                    paper_sell(market["bid"], "automatic_trailing_stop")
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
async def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": VERSION,
        "execution_mode": EXECUTION_MODE,
        "updated_at": state.get("updated_at"),
    }


@app.get("/api/status")
async def status() -> dict[str, Any]:
    async with state_lock:
        return state


@app.post("/api/buy")
async def buy() -> dict[str, Any]:
    market = await get_market()
    async with state_lock:
        paper_buy(market["ask"], "manual")
        mark_equity(market["price"])
        save_state()
        return state


@app.post("/api/sell")
async def sell() -> dict[str, Any]:
    market = await get_market()
    async with state_lock:
        paper_sell(market["bid"], "manual")
        mark_equity(market["price"])
        save_state()
        return state


@app.post("/api/auto/start")
async def auto_start() -> dict[str, Any]:
    async with state_lock:
        state["auto_trading"] = True
        save_state()
        return state


@app.post("/api/auto/stop")
async def auto_stop() -> dict[str, Any]:
    async with state_lock:
        state["auto_trading"] = False
        save_state()
        return state


@app.post("/api/reset")
async def reset() -> dict[str, Any]:
    async with state_lock:
        state.clear()
        state.update(initial_state())
        save_state()
        return state
