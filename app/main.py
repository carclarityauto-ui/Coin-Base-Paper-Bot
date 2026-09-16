import asyncio,json,os
from datetime import datetime,timezone
from pathlib import Path
import httpx,numpy as np,pandas as pd
from fastapi import FastAPI,HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

VERSION="Multi-Crypto High-Risk Paper Scalper V4"
app=FastAPI(title=VERSION)
app.mount("/static",StaticFiles(directory="app/static"),name="static")
f=lambda n,d: float(os.getenv(n,str(d)))
i=lambda n,d: int(os.getenv(n,str(d)))
PRODUCTS=[x.strip().upper() for x in os.getenv("PRODUCTS","BTC-USD,ETH-USD,SOL-USD,XRP-USD").split(",") if x.strip()]
STARTING_CASH=f("STARTING_CASH",5000)
MAX_POSITION_PCT=f("MAX_POSITION_PCT",.25)
FEE=f("FEE_PCT_PER_SIDE",.001)
SLIP=f("SLIPPAGE_PCT_PER_SIDE",.0003)
POLL=max(3,i("POLL_SECONDS",5))
COOLDOWN=max(3,i("MIN_SECONDS_BETWEEN_ENTRIES",5))
ENTRY=f("ENTRY_SCORE",.02)
STOP=f("STOP_LOSS_PCT",.006)
NET_TARGET=f("NET_PROFIT_TARGET_PCT",.0010)
MIN_GROSS_TARGET=f("MIN_GROSS_TARGET_PCT",.0025)
MAX_HOLD=max(30,i("MAX_HOLD_SECONDS",180))
DAILY_LOSS=f("DAILY_LOSS_LIMIT_PCT",.08)
AUTO=os.getenv("AUTO_TRADING","false").lower()=="true"
MODE=os.getenv("EXECUTION_MODE","paper").lower()
PATH=Path(os.getenv("DATA_PATH","/data/state.json"))
if MODE!="paper": raise RuntimeError("Paper execution only.")
if not .01<=MAX_POSITION_PCT<=.25: raise RuntimeError("Bad position cap.")
if not 0<=FEE<=.02 or not 0<=SLIP<=.01: raise RuntimeError("Bad fee/slippage.")
client=httpx.AsyncClient(base_url="https://api.exchange.coinbase.com",timeout=10,headers={"User-Agent":"multicrypto-high-risk-paper-v4"})
lock=asyncio.Lock()
now=lambda: datetime.now(timezone.utc).isoformat()
today=lambda: datetime.now(timezone.utc).date().isoformat()

def fresh():
    return {"version":VERSION,"products":PRODUCTS,"cash":STARTING_CASH,"equity":STARTING_CASH,
    "day_start_equity":STARTING_CASH,"day":today(),"position":None,"last_prices":{},
    "rankings":[],"fees_paid":0.0,"realized_pnl":0.0,"daily_pnl":0.0,"entries_today":0,
    "trades":[],"auto_trading":AUTO,"daily_lock":False,"last_entry_time":None,
    "updated_at":None,"last_error":None}

def load():
    try:
        if PATH.exists():
            x=fresh(); x.update(json.loads(PATH.read_text())); x["version"]=VERSION; x["products"]=PRODUCTS; return x
    except: pass
    return fresh()
state=load()
def save():
    PATH.parent.mkdir(parents=True,exist_ok=True); t=PATH.with_suffix(".tmp"); t.write_text(json.dumps(state,indent=2)); t.replace(PATH)
def reset_day():
    if state["day"]!=today():
        state["day"]=today(); state["day_start_equity"]=state["equity"]; state["daily_pnl"]=0; state["entries_today"]=0; state["daily_lock"]=False
def rsi(c,p=10):
    d=c.diff(); g=d.clip(lower=0).ewm(alpha=1/p,adjust=False).mean(); l=(-d.clip(upper=0)).ewm(alpha=1/p,adjust=False).mean()
    return (100-100/(1+g/l.replace(0,np.nan))).fillna(50)

async def fetch(prod):
    tr,cr=await asyncio.gather(client.get(f"/products/{prod}/ticker"),client.get(f"/products/{prod}/candles",params={"granularity":60}))
    tr.raise_for_status(); cr.raise_for_status(); t=tr.json()
    df=pd.DataFrame(cr.json(),columns=["time","low","high","open","close","volume"]).sort_values("time").tail(100)
    if len(df)<35: raise RuntimeError("insufficient data")
    return prod,float(t["price"]),float(t["bid"]),float(t["ask"]),df

def score(item):
    prod,price,bid,ask,df=item
    c=df.close.astype(float); v=df.volume.astype(float); ret=c.pct_change()
    e5=c.ewm(span=5,adjust=False).mean(); e13=c.ewm(span=13,adjust=False).mean(); e34=c.ewm(span=34,adjust=False).mean()
    trend=(e5.iloc[-1]-e13.iloc[-1])/c.iloc[-1]; regime=(e13.iloc[-1]-e34.iloc[-1])/c.iloc[-1]
    mom=float(ret.tail(3).mean() or 0); vol=float(ret.tail(15).std() or 0); vr=float(v.iloc[-1]/max(v.tail(15).mean(),1e-12)); rr=float(rsi(c).iloc[-1])
    spread=max(0,(ask-bid)/price); clip=lambda x: float(np.clip(x,-1,1))
    s=.30*clip(trend/.001)+.22*clip(mom/.001)+.18*clip(regime/.0015)+.15*clip((vr-1)/.35)+.15*clip((rr-50)/18)
    cost=2*FEE+2*SLIP+spread; est=max(vol*2.5,abs(trend)*2.2,abs(mom)*2.5)
    gross_target=max(MIN_GROSS_TARGET,cost+NET_TARGET)
    blockers=[]
    if s < ENTRY: blockers.append(f"score {s:.3f} < {ENTRY:.3f}")
    if est < .0005: blockers.append(f"estimated move {est*100:.3f}% < 0.050%")
    if not (.00002 <= vol <= .08): blockers.append(f"volatility {vol*100:.3f}% outside range")
    if not (35 <= rr <= 88): blockers.append(f"RSI {rr:.1f} outside 35-88")
    return {"product":prod,"price":price,"bid":bid,"ask":ask,"score":s,"rsi":rr,
    "estimated_move_pct":est*100,"round_trip_cost_pct":cost*100,"gross_target_pct":gross_target*100,"net_target_pct":NET_TARGET*100,
    "blockers":blockers,"buy":bool(not blockers),
    "sell":bool(s<=-.08 or rr>=90)}

async def scan():
    xs=await asyncio.gather(*[fetch(p) for p in PRODUCTS],return_exceptions=True)
    sig=[score(x) for x in xs if not isinstance(x,Exception)]
    return sorted(sig,key=lambda x:x["score"],reverse=True)

def mark():
    p=state["position"]
    if p:
        px=state["last_prices"].get(p["product"],p["entry_price"]); state["equity"]=state["cash"]+p["quantity"]*px
    else: state["equity"]=state["cash"]
    state["daily_pnl"]=state["equity"]-state["day_start_equity"]; state["daily_lock"]=state["daily_pnl"]<=-state["day_start_equity"]*DAILY_LOSS

def can_enter():
    if state["position"] or state["daily_lock"]: return False
    if state["last_entry_time"]:
        if (datetime.now(timezone.utc)-datetime.fromisoformat(state["last_entry_time"])).total_seconds()<COOLDOWN: return False
    return True

def buy(sig,reason):
    if not can_enter(): raise HTTPException(400,"Entry blocked.")
    equity=state["equity"]; notional=min(equity*MAX_POSITION_PCT,state["cash"]/(1+FEE))
    fill=sig["ask"]*(1+SLIP); fee=notional*FEE; qty=notional/fill; debit=notional+fee
    if debit>state["cash"]: raise HTTPException(400,"Insufficient cash.")
    state["cash"]-=debit; state["fees_paid"]+=fee
    state["position"]={"product":sig["product"],"quantity":qty,"entry_price":fill,"entry_fee":fee,"entry_time":now(),"highest_price":fill,"entry_equity":equity}
    state["entries_today"]+=1; state["last_entry_time"]=now()
    state["trades"].append({"time":now(),"side":"BUY","product":sig["product"],"notional":notional,"price":fill,"fee":fee,"pnl":0,"reason":reason}); mark()
    if state["cash"]<0: raise RuntimeError("Negative cash safety failure.")

def sell(sig,reason):
    p=state["position"]
    if not p: raise HTTPException(400,"No position.")
    fill=sig["bid"]*(1-SLIP); proceeds=p["quantity"]*fill; fee=proceeds*FEE
    pnl=(fill-p["entry_price"])*p["quantity"]-p["entry_fee"]-fee
    state["cash"]+=proceeds-fee; state["fees_paid"]+=fee; state["realized_pnl"]+=pnl; state["position"]=None
    state["trades"].append({"time":now(),"side":"SELL","product":sig["product"],"notional":proceeds,"price":fill,"fee":fee,"pnl":pnl,"reason":reason}); mark()

async def update():
    sigs=await scan()
    if not sigs: raise RuntimeError("No configured markets available.")
    async with lock:
        reset_day(); state["rankings"]=sigs; state["last_prices"]={s["product"]:s["price"] for s in sigs}; state["updated_at"]=now(); state["last_error"]=None; mark()
        if state["position"]:
            p=state["position"]; cur=next((s for s in sigs if s["product"]==p["product"]),None)
            if cur: p["highest_price"]=max(p["highest_price"],cur["bid"])
        if state["auto_trading"]:
            if not state["position"] and can_enter():
                best=next((s for s in sigs if s["buy"]),None)
                if best: buy(best,"active_ranked_entry")
            elif state["position"]:
                p=state["position"]; cur=next((s for s in sigs if s["product"]==p["product"]),None)
                if cur:
                    move=cur["bid"]/p["entry_price"]-1; held=(datetime.now(timezone.utc)-datetime.fromisoformat(p["entry_time"])).total_seconds()
                    spread=max(0,(cur["ask"]-cur["bid"])/max(cur["price"],1e-12))
                    gross_target=max(MIN_GROSS_TARGET,2*FEE+2*SLIP+spread+NET_TARGET)
                    if move<=-STOP: sell(cur,"active_stop")
                    elif move>=gross_target: sell(cur,"net_profit_target")
                    elif held>=MAX_HOLD: sell(cur,"active_timeout")
                    elif cur["sell"]: sell(cur,"active_signal_exit")
        mark(); save()

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
@app.get("/")
async def index(): return FileResponse("app/static/index.html")
@app.get("/health")
async def health(): return {"ok":True,"version":VERSION,"mode":MODE}
@app.get("/api/status")
async def status():
    async with lock: return state
@app.post("/api/auto/start")
async def start():
    async with lock: state["auto_trading"]=True; save(); return state
@app.post("/api/auto/stop")
async def stop():
    async with lock: state["auto_trading"]=False; save(); return state
@app.post("/api/reset")
async def reset():
    async with lock: state.clear(); state.update(fresh()); save(); return state
