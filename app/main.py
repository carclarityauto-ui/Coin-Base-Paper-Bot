import asyncio,json,os,math
from datetime import datetime,timezone
from pathlib import Path
import httpx,numpy as np,pandas as pd
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

VERSION="Multi-Market Paper Lab V5"
app=FastAPI(title=VERSION); app.mount("/static",StaticFiles(directory="app/static"),name="static")
f=lambda n,d: float(os.getenv(n,str(d))); i=lambda n,d: int(os.getenv(n,str(d)))
START=f("STARTING_CASH_PER_MARKET",5000); POS=f("MAX_POSITION_PCT",.15); ENTRY=f("ENTRY_SCORE",.25)
STOP=f("STOP_LOSS_PCT",.006); TRAIL=f("TRAILING_STOP_PCT",.004); HOLD=i("MAX_HOLD_SECONDS",1800); DAYLOSS=f("DAILY_LOSS_LIMIT_PCT",.03)
AUTO=os.getenv("AUTO_TRADING","true").lower()=="true"; PATH=Path(os.getenv("DATA_PATH","/data/multimarket-v5.json"))
UNIVERSES={"crypto":["BTC-USD","ETH-USD","SOL-USD","XRP-USD"],"stocks":["SPY","QQQ","AAPL","NVDA"],"futures":["ES=F","NQ=F","YM=F","RTY=F"]}
COST_SIDE={"crypto":f("CRYPTO_FEE_PCT_PER_SIDE",.001)+f("CRYPTO_SLIPPAGE_PCT_PER_SIDE",.0003),"stocks":f("STOCK_COST_PCT_PER_SIDE",.0002),"futures":f("FUTURES_COST_PCT_PER_SIDE",.0002)}
client=httpx.AsyncClient(timeout=12,headers={"User-Agent":"Mozilla/5.0 MultiMarketPaperLab/5"}); lock=asyncio.Lock()
now=lambda: datetime.now(timezone.utc).isoformat(); today=lambda: datetime.now(timezone.utc).date().isoformat()
def sleeve(): return {"cash":START,"equity":START,"day_start":START,"daily_pnl":0,"realized_pnl":0,"gross_pnl":0,"costs":0,"position":None,"entries":0,"wins":0,"losses":0,"peak":START,"max_drawdown_pct":0,"rankings":[],"trades":[],"daily_lock":False}
def fresh(): return {"version":VERSION,"day":today(),"auto_trading":AUTO,"updated_at":None,"last_error":None,"sleeves":{k:sleeve() for k in UNIVERSES}}
def load():
    try:
        if PATH.exists():
            x=fresh(); old=json.loads(PATH.read_text()); x.update({k:v for k,v in old.items() if k!="sleeves"});
            for k in x["sleeves"]:
                if k in old.get("sleeves",{}): x["sleeves"][k].update(old["sleeves"][k])
            x["version"]=VERSION; return x
    except: pass
    return fresh()
state=load()
def save(): PATH.parent.mkdir(parents=True,exist_ok=True); tmp=PATH.with_suffix('.tmp'); tmp.write_text(json.dumps(state,indent=2)); tmp.replace(PATH)
def rsi(c,p=10):
    d=c.diff(); g=d.clip(lower=0).ewm(alpha=1/p,adjust=False).mean(); l=(-d.clip(upper=0)).ewm(alpha=1/p,adjust=False).mean(); return (100-100/(1+g/l.replace(0,np.nan))).fillna(50)
async def coinbase(sym):
    base="https://api.exchange.coinbase.com"; tr,cr=await asyncio.gather(client.get(base+f"/products/{sym}/ticker"),client.get(base+f"/products/{sym}/candles",params={"granularity":300}))
    tr.raise_for_status(); cr.raise_for_status(); t=tr.json(); df=pd.DataFrame(cr.json(),columns=["time","low","high","open","close","volume"]).sort_values("time").tail(100)
    return float(t["price"]),df
async def yahoo(sym):
    u=f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"; r=await client.get(u,params={"range":"5d","interval":"5m","includePrePost":"false"}); r.raise_for_status(); z=r.json()["chart"]["result"][0]
    q=z["indicators"]["quote"][0]; df=pd.DataFrame({"time":z["timestamp"],"close":q["close"],"volume":q.get("volume",[]) or [0]*len(z["timestamp"])}).dropna().tail(100)
    if len(df)<35: raise RuntimeError(f"insufficient data {sym}")
    return float(df.close.iloc[-1]),df
async def fetch(m,s):
    p,df=await (coinbase(s) if m=="crypto" else yahoo(s)); return m,s,p,df

def score(item):
    m,s,p,df=item; c=df.close.astype(float); v=pd.Series(df.volume).astype(float).fillna(0); ret=c.pct_change(); e5=c.ewm(span=5,adjust=False).mean(); e13=c.ewm(span=13,adjust=False).mean(); e34=c.ewm(span=34,adjust=False).mean()
    trend=float((e5.iloc[-1]-e13.iloc[-1])/c.iloc[-1]); regime=float((e13.iloc[-1]-e34.iloc[-1])/c.iloc[-1]); mom=float(ret.tail(3).mean() or 0); vol=float(ret.tail(20).std() or 0); rr=float(rsi(c).iloc[-1]); vr=float(v.iloc[-1]/max(v.tail(20).mean(),1e-12)) if v.tail(20).mean()>0 else 1
    clip=lambda x:float(np.clip(x,-1,1)); sc=.32*clip(trend/.0015)+.25*clip(mom/.0015)+.18*clip(regime/.002)+.10*clip((vr-1)/.5)+.15*clip((rr-50)/20)
    cost=2*COST_SIDE[m]; est=max(vol*2.2,abs(trend)*2.0,abs(mom)*2.2); required=max(.0010,cost*1.5+.0005); blockers=[]
    if sc<ENTRY: blockers.append(f"score {sc:.3f} < {ENTRY:.3f}")
    if est<required: blockers.append(f"expected {est*100:.3f}% < required {required*100:.3f}%")
    if not (38<=rr<=78): blockers.append(f"RSI {rr:.1f} outside 38-78")
    return {"symbol":s,"price":p,"score":sc,"rsi":rr,"expected_move_pct":est*100,"round_trip_cost_pct":cost*100,"required_move_pct":required*100,"blockers":blockers,"buy":not blockers,"sell":sc<-.15 or rr>82}

def mark(m,prices):
    x=state["sleeves"][m]; p=x["position"]; x["equity"]=x["cash"]+(p["quantity"]*prices.get(p["symbol"],p["entry_price"]) if p else 0); x["daily_pnl"]=x["equity"]-x["day_start"]; x["peak"]=max(x["peak"],x["equity"]); x["max_drawdown_pct"]=max(x["max_drawdown_pct"],(x["peak"]-x["equity"])/max(x["peak"],1)*100); x["daily_lock"]=x["daily_pnl"]<=-x["day_start"]*DAYLOSS

def buy(m,sig):
    x=state["sleeves"][m]; notional=min(x["equity"]*POS,x["cash"]/(1+COST_SIDE[m])); fee=notional*COST_SIDE[m]; qty=notional/sig["price"]; x["cash"]-=notional+fee; x["costs"]+=fee; x["position"]={"symbol":sig["symbol"],"quantity":qty,"entry_price":sig["price"],"entry_fee":fee,"entry_time":now(),"highest":sig["price"]}; x["entries"]+=1; x["trades"].append({"time":now(),"side":"BUY","symbol":sig["symbol"],"notional":notional,"cost":fee,"net_pnl":0,"reason":"cost_aware_entry"})
def sell(m,sig,reason):
    x=state["sleeves"][m]; p=x["position"]; proceeds=p["quantity"]*sig["price"]; fee=proceeds*COST_SIDE[m]; gross=(sig["price"]-p["entry_price"])*p["quantity"]; net=gross-p["entry_fee"]-fee; x["cash"]+=proceeds-fee; x["costs"]+=fee; x["gross_pnl"]+=gross; x["realized_pnl"]+=net; x["wins"]+=int(net>0); x["losses"]+=int(net<=0); x["position"]=None; x["trades"].append({"time":now(),"side":"SELL","symbol":sig["symbol"],"notional":proceeds,"cost":fee,"gross_pnl":gross,"net_pnl":net,"reason":reason})

async def update():
    tasks=[fetch(m,s) for m,ss in UNIVERSES.items() for s in ss]; raw=await asyncio.gather(*tasks,return_exceptions=True); grouped={m:[] for m in UNIVERSES}
    for q in raw:
        if not isinstance(q,Exception): grouped[q[0]].append(score(q))
    async with lock:
        if state["day"]!=today():
            state["day"]=today()
            for x in state["sleeves"].values(): x["day_start"]=x["equity"]; x["daily_pnl"]=0; x["daily_lock"]=False
        for m,sigs in grouped.items():
            sigs.sort(key=lambda z:z["score"],reverse=True); x=state["sleeves"][m]; x["rankings"]=sigs; prices={s["symbol"]:s["price"] for s in sigs}; mark(m,prices)
            if not state["auto_trading"] or not sigs: continue
            if not x["position"] and not x["daily_lock"]:
                best=next((s for s in sigs if s["buy"]),None)
                if best: buy(m,best)
            elif x["position"]:
                p=x["position"]; cur=next((s for s in sigs if s["symbol"]==p["symbol"]),None)
                if cur:
                    p["highest"]=max(p["highest"],cur["price"]); move=cur["price"]/p["entry_price"]-1; held=(datetime.now(timezone.utc)-datetime.fromisoformat(p["entry_time"])).total_seconds(); target=max(cur["required_move_pct"]/100,.002)
                    if move<=-STOP: sell(m,cur,"stop")
                    elif move>=target: sell(m,cur,"cost_aware_target")
                    elif p["highest"]>p["entry_price"]*(1+target*.65) and cur["price"]<=p["highest"]*(1-TRAIL): sell(m,cur,"trailing_exit")
                    elif held>=HOLD and move>0: sell(m,cur,"profitable_timeout")
                    elif held>=HOLD*2: sell(m,cur,"hard_timeout")
                    elif cur["sell"]: sell(m,cur,"signal_exit")
            mark(m,prices)
        state["updated_at"]=now(); state["last_error"]=None; save()
async def loop():
    while True:
        try: await update()
        except Exception as e:
            async with lock: state["last_error"]=str(e); state["updated_at"]=now(); save()
        await asyncio.sleep(20)
@app.on_event("startup")
async def startup(): asyncio.create_task(loop())
@app.on_event("shutdown")
async def shutdown(): await client.aclose()
@app.get("/")
async def index(): return FileResponse("app/static/index.html")
@app.get("/health")
async def health(): return {"ok":True,"version":VERSION,"paper_only":True}
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
