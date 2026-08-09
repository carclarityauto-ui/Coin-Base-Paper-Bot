import asyncio, json, math, os, statistics
from datetime import datetime, timezone
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Railway Coinbase Paper Bot")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

PRODUCT = os.getenv("PRODUCT_ID", "BTC-USD")
STARTING = float(os.getenv("STARTING_CASH", "250"))
POSITION_PCT = float(os.getenv("POSITION_PCT", "0.10"))
FEE = float(os.getenv("FEE_PCT_PER_SIDE", "0.60")) / 100
SLIPPAGE = float(os.getenv("SLIPPAGE_PCT_PER_SIDE", "0.03")) / 100
POLL = max(10, int(os.getenv("POLL_SECONDS", "15")))
EDGE_MULTIPLE = float(os.getenv("MIN_EDGE_MULTIPLE", "2.5"))
ENTRY_SCORE = float(os.getenv("ENTRY_SCORE", "0.62"))
STATE_PATH = Path(os.getenv("DATA_PATH", "/data/state.json"))
DEFAULT_AUTO = os.getenv("AUTO_TRADING", "false").lower() == "true"
client = httpx.AsyncClient(base_url="https://api.exchange.coinbase.com", timeout=15, headers={"User-Agent":"railway-paper-bot/1.0"})
lock = asyncio.Lock()

def now(): return datetime.now(timezone.utc).isoformat()
def fresh():
    return {"product_id":PRODUCT,"cash":STARTING,"equity":STARTING,"position":None,"fees_paid":0.0,"realized_pnl":0.0,"trades":[],"auto_trading":DEFAULT_AUTO,"last_price":None,"last_signal":None,"updated_at":None,"last_error":None}
def load():
    try:
        if STATE_PATH.exists(): return json.loads(STATE_PATH.read_text())
    except Exception: pass
    return fresh()
state = load()
def save():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp=STATE_PATH.with_suffix(".tmp"); tmp.write_text(json.dumps(state,indent=2)); tmp.replace(STATE_PATH)

def ema(values, period):
    k=2/(period+1); result=values[0]
    for value in values[1:]: result=value*k+result*(1-k)
    return result

def rsi(values, period=14):
    changes=[values[i]-values[i-1] for i in range(max(1,len(values)-period),len(values))]
    gains=sum(max(x,0) for x in changes)/max(1,len(changes)); losses=sum(max(-x,0) for x in changes)/max(1,len(changes))
    return 100 if losses==0 else 100-100/(1+gains/losses)

async def market():
    tr, cr = await asyncio.gather(client.get(f"/products/{PRODUCT}/ticker"), client.get(f"/products/{PRODUCT}/candles",params={"granularity":60}))
    tr.raise_for_status(); cr.raise_for_status(); t=tr.json(); rows=sorted(cr.json(),key=lambda x:x[0])[-120:]
    return {"price":float(t["price"]),"bid":float(t["bid"]),"ask":float(t["ask"]),"rows":rows}

def analyze(m):
    closes=[float(x[4]) for x in m["rows"]]; volumes=[float(x[5]) for x in m["rows"]]
    returns=[closes[i]/closes[i-1]-1 for i in range(1,len(closes))]
    fast=ema(closes[-40:],8); slow=ema(closes[-60:],21); trend=(fast-slow)/closes[-1]
    current_rsi=rsi(closes); vol=statistics.stdev(returns[-20:]) if len(returns)>=20 else 0
    vr=volumes[-1]/max(sum(volumes[-20:])/max(1,len(volumes[-20:])),1e-12); momentum=sum(returns[-3:])/max(1,len(returns[-3:]))
    spread=max(0,(m["ask"]-m["bid"])/m["price"]); clip=lambda x:max(-1,min(1,x))
    score=.4*clip(trend/.002)+.3*clip((current_rsi-50)/20)+.2*clip((vr-1)/.5)+.1*clip(momentum/.001)
    cost=2*FEE+2*SLIPPAGE+spread; move=max(vol*2,abs(trend)*1.5); gate=move>=cost*EDGE_MULTIPLE
    return {"score":score,"rsi":current_rsi,"trend_pct":trend*100,"volatility_pct":vol*100,"volume_ratio":vr,"spread_pct":spread*100,"estimated_move_pct":move*100,"round_trip_cost_pct":cost*100,"cost_gate":gate,"buy":score>=ENTRY_SCORE and gate and 52<=current_rsi<=72,"sell":score<=-.1 or current_rsi>=78}

def mark(price): state["equity"]=state["cash"]+(state["position"]["quantity"]*price if state.get("position") else 0)
def buy(price, reason):
    if state.get("position"): raise HTTPException(400,"A paper position is already open")
    budget=state["cash"]*POSITION_PCT; fill=price*(1+SLIPPAGE); fee=budget*FEE; qty=(budget-fee)/fill
    if qty<=0: raise HTTPException(400,"Paper balance is too small")
    state["cash"]-=qty*fill+fee; state["fees_paid"]+=fee; state["position"]={"quantity":qty,"entry_price":fill,"entry_fee":fee,"entry_time":now()}; state["trades"].append({"time":now(),"side":"BUY","price":fill,"quantity":qty,"fee":fee,"pnl":0,"reason":reason})
def sell(price, reason):
    p=state.get("position")
    if not p: raise HTTPException(400,"No paper position is open")
    fill=price*(1-SLIPPAGE); proceeds=p["quantity"]*fill; fee=proceeds*FEE; pnl=(fill-p["entry_price"])*p["quantity"]-p["entry_fee"]-fee
    state["cash"]+=proceeds-fee; state["fees_paid"]+=fee; state["realized_pnl"]+=pnl; state["position"]=None; state["trades"].append({"time":now(),"side":"SELL","price":fill,"quantity":p["quantity"],"fee":fee,"pnl":pnl,"reason":reason})

async def update():
    m=await market(); sig=analyze(m)
    async with lock:
        state.update(last_price=m["price"],last_signal=sig,updated_at=now(),last_error=None); mark(m["price"])
        if state.get("auto_trading"):
            if not state.get("position") and sig["buy"]: buy(m["ask"],"automatic_signal")
            elif state.get("position"):
                move=m["bid"]/state["position"]["entry_price"]-1; cost=sig["round_trip_cost_pct"]/100
                if move<=-max(.004,cost*1.25): sell(m["bid"],"automatic_stop")
                elif move>=max(.008,cost*2.5): sell(m["bid"],"automatic_target")
                elif sig["sell"]: sell(m["bid"],"automatic_signal_exit")
        mark(m["price"]); save()

async def loop():
    while True:
        try: await update()
        except Exception as e:
            async with lock: state["last_error"]=str(e); state["updated_at"]=now(); save()
        await asyncio.sleep(POLL)

@app.on_event("startup")
async def startup(): asyncio.create_task(loop())
@app.on_event("shutdown")
async def shutdown(): await client.aclose()
@app.get("/health")
async def health(): return {"ok":True,"updated_at":state.get("updated_at")}
@app.get("/")
async def index(): return FileResponse("app/static/index.html")
@app.get("/api/status")
async def status():
    async with lock: return state
@app.post("/api/buy")
async def manual_buy():
    m=await market()
    async with lock: buy(m["ask"],"manual"); mark(m["price"]); save(); return state
@app.post("/api/sell")
async def manual_sell():
    m=await market()
    async with lock: sell(m["bid"],"manual"); mark(m["price"]); save(); return state
@app.post("/api/auto/start")
async def start_auto():
    async with lock: state["auto_trading"]=True; save(); return state
@app.post("/api/auto/stop")
async def stop_auto():
    async with lock: state["auto_trading"]=False; save(); return state
@app.post("/api/reset")
async def reset():
    async with lock: state.clear(); state.update(fresh()); save(); return state
