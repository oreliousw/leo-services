#!/usr/bin/env python3
# ============================================================
# File: kraken_xrp.py
# Version: v2.6 — Per-Asset USD Slice Autopilot (XRP/USD)
#
# Asset-in-hand mode supported:
#   • If XRP balance > 0 and mode=idle → treat as HOLD position
#   • Heartbeats send even when usd_slice = 0
#
# Mode: LIVE — MARKET BUY/SELL (signals-free execution)
# Heartbeat: ~every 4h (status + swing tracking)
#
# Author: Orelious — Kraken MES XRP Line (2026)
# ============================================================

import os, sys, json, time, base64, hmac, hashlib, urllib.request
from pathlib import Path
from datetime import datetime

API_KEY_PUBLIC  = os.getenv("KRAKEN_API_KEY")
API_KEY_PRIVATE = os.getenv("KRAKEN_PRV_KEY")
TG_TOKEN = os.getenv("KRAKEN_TOKEN")
TG_CHAT  = os.getenv("TELEGRAM_ID")

if not all([API_KEY_PUBLIC, API_KEY_PRIVATE, TG_TOKEN, TG_CHAT]):
    print("[FATAL] Missing required environment variables.")
    sys.exit(1)

XRP_USD_SLICE_INIT = os.getenv("XRP_USD_SLICE_INIT")

PAIR = "XRPUSD"
MIN_USD_BALANCE = 10.0
SELL_FRACTION   = 0.25
DRY_RUN         = False

STATE_FILE = Path("kraken_state_xrp.json")
LOG_FILE   = Path("kraken_events_xrp.jsonl")

# ---------------- Utilities / Telegram / API ----------------
def log_event(ev):
    try:
        ev = dict(ev)
        ev.setdefault("timestamp_utc", datetime.utcnow().isoformat()+"Z")
        with LOG_FILE.open("a") as f:
            f.write(json.dumps(ev, default=str)+"\n")
    except Exception as e:
        print(f"[WARN] Log write failed: {e}")

def tg_send(msg):
    try:
        data = json.dumps({"chat_id": TG_CHAT, "text": msg}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req).read()
    except Exception as e:
        print(f"[WARN] Telegram send failed: {e}")

API_BASE = "https://api.kraken.com"

def k_public(path):
    with urllib.request.urlopen(API_BASE+path) as r:
        return json.loads(r.read().decode())

def k_private(path, params):
    nonce = str(int(time.time()*1000))
    post  = f"nonce={nonce}&{params}"
    sha = hashlib.sha256(nonce.encode()+post.encode())
    sig = hmac.new(
        base64.b64decode(API_KEY_PRIVATE),
        (path.encode()+sha.digest()),
        hashlib.sha512
    )
    signature = base64.b64encode(sig.digest())
    req = urllib.request.Request(f"{API_BASE}{path}", post.encode())
    req.add_header("API-Key", API_KEY_PUBLIC)
    req.add_header("API-Sign", signature)
    req.add_header("User-Agent", "Kraken-MES-xrp-v2.6")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())

def place_market_order(side, volume):
    volume_str = f"{volume:.8f}"
    if DRY_RUN:
        log_event({"event_type":"dry_run_order","side":side,"pair":PAIR,"volume":volume_str})
        print(f"[DRY-RUN] Would place {side} {PAIR} {volume_str}")
        return {"result":"dry_run"}
    res = k_private("/0/private/AddOrder", f"pair={PAIR}&type={side}&ordertype=market&volume={volume_str}")
    if res.get("error"): raise RuntimeError(res["error"])
    return res

# ---------------- Helpers / Pricing / Balances --------------
def pct(a,b): return ((b-a)/a)*100.0 if a else 0.0

def xrp_price_and_change():
    data = k_public("/0/public/Ticker?pair=XRPUSD")
    k = list(data["result"].keys())[0]
    last = float(data["result"][k]["c"][0])
    open_ = float(data["result"][k]["o"])
    return last, pct(open_, last)

def get_kraken_balances():
    res = k_private("/0/private/Balance","")
    if res.get("error"): raise RuntimeError(res["error"])
    return (
        float(res["result"].get("XXRP",0.0)),
        float(res["result"].get("ZUSD",0.0)),
    )

# ---------------- State ----------------
DEFAULT_STATE = {
    "mode":"idle","entry_price":None,"last_swing_high":None,
    "buy_approach_sent":False,"sell_approach_sent":False,
    "entry_time":None,"usd_slice":None,"last_heartbeat":None,
}

def load_state():
    s = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    base = DEFAULT_STATE.copy(); base.update(s)
    if base["usd_slice"] is None:
        try: base["usd_slice"] = float(XRP_USD_SLICE_INIT or 0)
        except: base["usd_slice"] = 0.0
    return base

def save_state(s): STATE_FILE.write_text(json.dumps(s,indent=2))

# ---------------- Thresholds ----------------
BUY_PULLBACK=-3.0; BUY_APPROACH=-2.5
SELL_TARGET=5.0; SELL_APPROACH=4.0
DRAWDOWN_RESET=-12.0
HEARTBEAT_INTERVAL_HOURS=4

# ---------------- Asset-in-hand support ----------------
def detect_initial_position(state, asset_balance, price):
    if asset_balance>0 and state["mode"]=="idle":
        if state["entry_price"] is None:
            state["entry_price"]=price
        state["mode"]="hold"
        state["sell_approach_sent"]=False

# ---------------- Heartbeat (always send) ----------------
def maybe_send_heartbeat(state, price):
    now=time.time(); last=state.get("last_heartbeat") or 0
    if now-last < HEARTBEAT_INTERVAL_HOURS*3600: return
    tg_send(
        "🔎 XRP Heartbeat — v2.6\n"
        f"Mode: {state['mode']}\n"
        f"Slice: ${state.get('usd_slice',0):.2f}\n"
        f"Price: {price:.4f}\n"
        f"Swing High: {state.get('last_swing_high',price):.4f}"
    )
    state["last_heartbeat"]=now

# ---------------- Trade Execution ----------------
def execute_buy(price,state):
    _,usd_bal=get_kraken_balances()
    slice_before=float(state["usd_slice"])
    usd_avail=min(slice_before, usd_bal)
    if usd_avail<MIN_USD_BALANCE: return False
    usd_to_spend=usd_avail
    volume=round(usd_to_spend/price,8)
    res=place_market_order("buy",volume)
    state["usd_slice"]=max(0.0,slice_before-usd_to_spend)
    state["entry_price"]=price; state["entry_time"]=time.time()
    state["mode"]="hold"; state["sell_approach_sent"]=False
    tg_send(
        "🟢 XRP BUY EXECUTED\n"
        f"Price: {price:.4f}\n"
        f"USD Spent: ${usd_to_spend:.2f}\n"
        f"XRP Bought: {volume:.8f}\n"
        f"USD Slice: ${slice_before:.2f} → ${state['usd_slice']:.2f}\n"
        "Engine v2.6"
    )
    log_event({"event_type":"xrp_buy","price":price,"volume":volume,"usd_spent":usd_to_spend})
    return True

def execute_sell(reason,price,state):
    xrp_bal,_=get_kraken_balances()
    volume=round(xrp_bal*SELL_FRACTION,8)
    notional=volume*price
    if notional<MIN_USD_BALANCE: return False
    res=place_market_order("sell",volume)
    slice_before=float(state["usd_slice"])
    state["usd_slice"]=slice_before+notional
    state["mode"]="reset"
    tg_send(
        f"🔵 XRP SELL ({reason})\n"
        f"Price: {price:.4f}\n"
        f"Sold: {volume:.8f}\n"
        f"Credited: ${notional:.2f}\n"
        f"USD Slice: ${slice_before:.2f} → ${state['usd_slice']:.2f}\n"
        "Engine v2.6"
    )
    log_event({"event_type":f"xrp_sell_{reason}","price":price,"volume":volume,"notional":notional})
    return True

# ---------------- Engine Tick ----------------
def engine_tick():
    s=load_state()
    price,_=xrp_price_and_change()
    xrp_bal,_=get_kraken_balances()

    if s["last_swing_high"] is None: s["last_swing_high"]=price
    if price>s["last_swing_high"]:
        s["last_swing_high"]=price; s["buy_approach_sent"]=False

    detect_initial_position(s, xrp_bal, price)
    maybe_send_heartbeat(s, price)

    pullback=pct(s["last_swing_high"], price)

    if s["mode"]=="idle":
        if not s["buy_approach_sent"] and pullback<=BUY_APPROACH:
            s["buy_approach_sent"]=True
        if pullback<=BUY_PULLBACK:
            execute_buy(price,s)

    elif s["mode"]=="hold":
        gain=pct(s["entry_price"], price)
        if gain<=DRAWDOWN_RESET: execute_sell("drawdown_reset",price,s)
        elif gain>=SELL_TARGET: execute_sell("target",price,s)

    elif s["mode"]=="reset":
        if pullback<=BUY_PULLBACK:
            s["mode"]="idle"; s["buy_approach_sent"]=False

    save_state(s)

if __name__=="__main__":
    try:
        print("Kraken XRP Trader v2.6 tick OK"); engine_tick()
    except Exception as e:
        tg_send(f"❌ Kraken XRP v2.6 runtime error:\n{e}")
        print(f"[FATAL] {e}"); sys.exit(1)
