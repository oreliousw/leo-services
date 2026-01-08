#!/usr/bin/env python3
# ============================================================
# File: kraken_btc.py
# Version: v2.6 — Per-Asset USD Slice Autopilot (BTC/USD)
#
# Mode: LIVE — MARKET BUY/SELL (signals-free execution)
# Slice Model:
#   • BUY spends only from btc.usd_slice
#   • SELL credits proceeds back to btc.usd_slice
#   • USD slice is isolated from other assets
#
# Heartbeat:
#   • Sends status ~every 4 hours
#   • Only if usd_slice > 0
#
# Author: Orelious — Kraken MES BTC Line (2026)
# ============================================================

import os, sys, json, time, base64, hmac, hashlib, urllib.request
from pathlib import Path
from datetime import datetime

# ------------------------------------------------------------
# Environment
# ------------------------------------------------------------
API_KEY_PUBLIC  = os.getenv("KRAKEN_API_KEY")
API_KEY_PRIVATE = os.getenv("KRAKEN_PRV_KEY")
TG_TOKEN = os.getenv("KRAKEN_TOKEN")
TG_CHAT  = os.getenv("TELEGRAM_ID")

if not all([API_KEY_PUBLIC, API_KEY_PRIVATE, TG_TOKEN, TG_CHAT]):
    print("[FATAL] Missing required environment variables.")
    sys.exit(1)

CORE_BTC_REFERENCE = float(os.getenv("CORE_BTC_REFERENCE", "0.0"))
BTC_USD_SLICE_INIT = os.getenv("BTC_USD_SLICE_INIT")

REPORT_HOUR = 6
REPORT_MIN  = 0

# ------------------------------------------------------------
# Trading Constants
# ------------------------------------------------------------
PAIR = "XBTUSD"
MIN_USD_BALANCE = 10.0
SELL_FRACTION   = 0.25
DRY_RUN         = False

STATE_FILE = Path("kraken_state_btc.json")
SNAP_FILE  = Path("portfolio_snapshot_btc.json")
LOG_FILE   = Path("kraken_events_btc.jsonl")

# ------------------------------------------------------------
# Utilities / Logging / Telegram
# ------------------------------------------------------------
def log_event(ev: dict):
    try:
        ev = dict(ev)
        ev.setdefault("timestamp_utc", datetime.utcnow().isoformat() + "Z")
        with LOG_FILE.open("a") as f:
            f.write(json.dumps(ev, default=str) + "\n")
    except Exception as e:
        print(f"[WARN] Log write failed: {e}")

def tg_send(msg: str):
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

# ------------------------------------------------------------
# Kraken API
# ------------------------------------------------------------
API_BASE = "https://api.kraken.com"

def k_public(path: str):
    with urllib.request.urlopen(API_BASE + path) as resp:
        return json.loads(resp.read().decode())

def k_private(path: str, params: str):
    nonce = str(int(time.time() * 1000))
    post  = f"nonce={nonce}&{params}"
    sha = hashlib.sha256(nonce.encode() + post.encode())
    sig = hmac.new(
        base64.b64decode(API_KEY_PRIVATE),
        (path.encode() + sha.digest()),
        hashlib.sha512
    )
    signature = base64.b64encode(sig.digest())

    req = urllib.request.Request(f"{API_BASE}{path}", post.encode())
    req.add_header("API-Key", API_KEY_PUBLIC)
    req.add_header("API-Sign", signature)
    req.add_header("User-Agent", "Kraken-MES-btc-v2.6")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def place_market_order(side: str, volume: float):
    volume_str = f"{volume:.8f}"

    if DRY_RUN:
        log_event({
            "event_type": "dry_run_order",
            "engine_version": "v2.6",
            "side": side,
            "pair": PAIR,
            "volume": volume_str,
        })
        print(f"[DRY-RUN] Would place {side} {PAIR} {volume_str}")
        return {"result": "dry_run"}

    params = f"pair={PAIR}&type={side}&ordertype=market&volume={volume_str}"
    res = k_private("/0/private/AddOrder", params)
    if res.get("error"):
        raise RuntimeError(res["error"])
    return res

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def pct(a, b):
    return ((b - a) / a) * 100.0 if a else 0.0

def btc_price_and_change():
    data = k_public("/0/public/Ticker?pair=XBTUSD")
    k = list(data["result"].keys())[0]
    last = float(data["result"][k]["c"][0])
    open_24h = float(data["result"][k]["o"])
    return last, pct(open_24h, last)

def get_kraken_balances():
    res = k_private("/0/private/Balance", "")
    if res.get("error"):
        raise RuntimeError(res["error"])
    return (
        float(res["result"].get("XXBT", 0.0)),
        float(res["result"].get("ZUSD", 0.0)),
    )

# ------------------------------------------------------------
# State + Snapshot
# ------------------------------------------------------------
DEFAULT_STATE = {
    "mode": "idle",
    "entry_price": None,
    "last_swing_high": None,
    "buy_approach_sent": False,
    "sell_approach_sent": False,
    "entry_time": None,
    "usd_slice": None,
    "last_heartbeat": None,
}

def load_state():
    if STATE_FILE.exists():
        s = json.loads(STATE_FILE.read_text())
    else:
        s = {}

    base = DEFAULT_STATE.copy()
    base.update(s)

    if base["usd_slice"] is None:
        if BTC_USD_SLICE_INIT:
            try:
                base["usd_slice"] = float(BTC_USD_SLICE_INIT)
            except:
                base["usd_slice"] = 0.0
        else:
            base["usd_slice"] = 0.0

    return base

def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2))

# ------------------------------------------------------------
# Thresholds
# ------------------------------------------------------------
BUY_PULLBACK   = -3.0
BUY_APPROACH   = -2.5
SELL_TARGET    =  5.0
SELL_APPROACH  =  4.0
DRAWDOWN_RESET = -12.0

# ------------------------------------------------------------
# Heartbeat (only when slice > 0)
# ------------------------------------------------------------
HEARTBEAT_INTERVAL_HOURS = 4

def maybe_send_heartbeat(state, price):
    if (state.get("usd_slice") or 0) <= 0:
        return  # no capital — stay quiet

    now = time.time()
    last = state.get("last_heartbeat") or 0
    if now - last < HEARTBEAT_INTERVAL_HOURS * 3600:
        return

    tg_send(
        "🔎 BTC Heartbeat — v2.6\n"
        f"Mode: {state['mode']}\n"
        f"Slice: ${state['usd_slice']:.2f}\n"
        f"Price: {price:.2f}\n"
        f"Swing High: {state['last_swing_high']:.2f}"
    )

    state["last_heartbeat"] = now

# ------------------------------------------------------------
# Trade Execution (USD Slice Model)
# ------------------------------------------------------------
def execute_buy(price, state):
    btc_bal, usd_bal = get_kraken_balances()
    slice_before = float(state["usd_slice"])
    usd_avail = min(slice_before, usd_bal)

    if usd_avail < MIN_USD_BALANCE:
        return False

    usd_to_spend = usd_avail
    volume = round(usd_to_spend / price, 8)

    res = place_market_order("buy", volume)

    state["usd_slice"] = max(0.0, slice_before - usd_to_spend)
    state["entry_price"] = price
    state["entry_time"] = time.time()
    state["mode"] = "hold"
    state["sell_approach_sent"] = False

    tg_send(
        "🟢 BTC BUY EXECUTED\n"
        f"Price: {price:.2f}\n"
        f"USD Spent: ${usd_to_spend:.2f}\n"
        f"BTC Bought: {volume:.8f}\n"
        f"USD Slice: ${slice_before:.2f} → ${state['usd_slice']:.2f}\n"
        "Engine v2.6"
    )

    log_event({
        "event_type": "btc_buy",
        "engine_version": "v2.6",
        "price": price,
        "volume": volume,
        "usd_spent": usd_to_spend,
        "slice_before": slice_before,
        "slice_after": state["usd_slice"],
        "response": res,
    })
    return True

def execute_sell(reason, price, state):
    btc_bal, _ = get_kraken_balances()
    volume = round(btc_bal * SELL_FRACTION, 8)
    notional = volume * price
    if notional < MIN_USD_BALANCE:
        return False

    res = place_market_order("sell", volume)

    slice_before = float(state["usd_slice"])
    state["usd_slice"] = slice_before + notional
    state["mode"] = "reset"

    tg_send(
        f"🔵 BTC SELL ({reason})\n"
        f"Price: {price:.2f}\n"
        f"Sold: {volume:.8f}\n"
        f"Credited: ${notional:.2f}\n"
        f"USD Slice: ${slice_before:.2f} → ${state['usd_slice']:.2f}\n"
        "Engine v2.6"
    )

    log_event({
        "event_type": f"btc_sell_{reason}",
        "engine_version": "v2.6",
        "price": price,
        "volume": volume,
        "notional": notional,
        "slice_before": slice_before,
        "slice_after": state["usd_slice"],
        "response": res,
    })
    return True

# ------------------------------------------------------------
# Engine Tick
# ------------------------------------------------------------
def engine_tick():
    s = load_state()
    price, _ = btc_price_and_change()

    if s["last_swing_high"] is None:
        s["last_swing_high"] = price

    if price > s["last_swing_high"]:
        s["last_swing_high"] = price
        s["buy_approach_sent"] = False

    maybe_send_heartbeat(s, price)

    pullback = pct(s["last_swing_high"], price)

    if s["mode"] == "idle":
        if not s["buy_approach_sent"] and pullback <= BUY_APPROACH:
            s["buy_approach_sent"] = True
        if pullback <= BUY_PULLBACK:
            execute_buy(price, s)

    elif s["mode"] == "hold":
        gain = pct(s["entry_price"], price)
        if gain <= DRAWDOWN_RESET:
            execute_sell("drawdown_reset", price, s)
        elif gain >= SELL_TARGET:
            execute_sell("target", price, s)

    elif s["mode"] == "reset":
        if pullback <= BUY_PULLBACK:
            s["mode"] = "idle"
            s["buy_approach_sent"] = False

    save_state(s)

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
if __name__ == "__main__":
    try:
        print("Kraken BTC Trader v2.6 tick OK")
        engine_tick()
    except Exception as e:
        tg_send(f"❌ Kraken BTC v2.6 runtime error:\n{e}")
        print(f"[FATAL] {e}")
        sys.exit(1)
