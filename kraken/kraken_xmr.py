#!/usr/bin/env python3
# ============================================================
# File: kraken_xmr.py
# Version: v2.8.2 — Centralized Sell Policy via usd_allocator
#
# v2.8.2 changes:
#   • Removed hard-coded SELL_FRACTION
#   • Sell sizing now pulled from usd_allocator.get_sell_fraction()
# ============================================================

import os, sys, json, time, base64, hmac, hashlib, urllib.request
from pathlib import Path
from datetime import datetime
from kraken_nonce import get_nonce
from usd_allocator import get_allocatable_usd, get_sell_fraction

ENGINE_VERSION = "v2.8.2"

print("[kraken] using shared nonce file /tmp/kraken_nonce.txt")

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

# ------------------------------------------------------------
# Trading Constants
# ------------------------------------------------------------
ASSET = "XMR"
PAIR = "XMRUSD"

MIN_USD_BALANCE = 10.0
DRY_RUN         = False

STATE_FILE = Path("kraken_state_xmr.json")
LOG_FILE   = Path("kraken_events_xmr.jsonl")

# ------------------------------------------------------------
# Utilities
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
    nonce = get_nonce()
    postdata = f"nonce={nonce}&{params}"

    sha = hashlib.sha256(
        str(nonce).encode() + postdata.encode()
    ).digest()

    sig = hmac.new(
        base64.b64decode(API_KEY_PRIVATE),
        path.encode() + sha,
        hashlib.sha512
    ).digest()

    signature = base64.b64encode(sig)

    req = urllib.request.Request(f"{API_BASE}{path}", postdata.encode())
    req.add_header("API-Key", API_KEY_PUBLIC)
    req.add_header("API-Sign", signature)
    req.add_header("User-Agent", f"Kraken-MES-xmr-{ENGINE_VERSION}")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def place_market_order(side: str, volume: float):
    volume_str = f"{volume:.8f}"

    if DRY_RUN:
        log_event({
            "event_type": "dry_run_order",
            "engine_version": ENGINE_VERSION,
            "side": side,
            "pair": PAIR,
            "volume": volume_str
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

def fmt_usd(x): return f"${x:,.2f}"
def fmt_pct(x): return f"{x:+.2f}%"

def xmr_price_and_change():
    data = k_public("/0/public/Ticker?pair=XMRUSD")
    k = list(data["result"].keys())[0]
    last = float(data["result"][k]["c"][0])
    open_24h = float(data["result"][k]["o"])
    return last, pct(open_24h, last)

# ------------------------------------------------------------
# Balance Cache (60s)
# ------------------------------------------------------------
BAL_CACHE_TTL = 60
_BAL = {"ts": 0.0, "xmr": 0.0, "usd": 0.0}

def is_rate_limited(msg: str) -> bool:
    m = (msg or "").lower()
    return ("rate" in m) or ("too many requests" in m)

def get_balances(force=False):
    now = time.time()
    if not force and now - _BAL["ts"] < BAL_CACHE_TTL:
        return _BAL["xmr"], _BAL["usd"]

    res = k_private("/0/private/Balance", "")
    if res.get("error"):
        raise RuntimeError(res["error"])

    r = res["result"]
    xmr = float(r.get("XXMR", 0.0))
    usd = float(r.get("ZUSD", 0.0))

    _BAL.update({"ts": now, "xmr": xmr, "usd": usd})
    return xmr, usd

# ------------------------------------------------------------
# State
# ------------------------------------------------------------
DEFAULT_STATE = {
    "mode": "idle",
    "entry_price": None,
    "last_swing_high": None,
    "last_swing_low": None,
    "buy_approach_sent": False,
    "sell_approach_sent": False,
    "entry_time": None,
    "last_heartbeat": None,
}

def load_state():
    if STATE_FILE.exists():
        return {**DEFAULT_STATE, **json.loads(STATE_FILE.read_text())}
    return DEFAULT_STATE.copy()

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
HEARTBEAT_INTERVAL_HOURS = 6

# ------------------------------------------------------------
# Heartbeat
# ------------------------------------------------------------
def maybe_send_heartbeat(state, price, xmr_bal, usd_bal):
    now = time.time()
    last = state.get("last_heartbeat")
    interval = HEARTBEAT_INTERVAL_HOURS * 3600

    if last and (now - float(last) < interval):
        return

    mode = state.get("mode", "idle")
    pos_usd = xmr_bal * price
    anchor = state.get("last_swing_low") or state.get("entry_price")
    gain = pct(anchor, price) if anchor else 0.0

    try:
        slice_usd = get_allocatable_usd(
            asset=ASSET,
            usd_total_available=usd_bal,
            usd_committed_by_asset=pos_usd if mode == "hold" else 0.0,
        )
    except Exception:
        slice_usd = 0.0

    sh = state.get("last_swing_high")
    sl = state.get("last_swing_low")

    if sh and sl:
        swing = f"Swing H/L: {sh:,.2f} / {sl:,.2f}"
    else:
        swing = None

    msg = (
        f"🫀 XMR Heartbeat — {ENGINE_VERSION}\n"
        f"Mode: {mode}\n"
        f"XMR: {xmr_bal:.8f}\n"
        f"USD: {fmt_usd(usd_bal)}\n"
        f"Pos: {fmt_usd(pos_usd)} @ {price:,.2f}\n"
        f"PnL (vs anchor {anchor:,.2f}): {fmt_pct(gain)}\n"
        f"Slice: {fmt_usd(slice_usd)}"
    )

    if swing:
        msg += f"\n{swing}"

    tg_send(msg)

    state["last_heartbeat"] = now

    log_event({
        "event_type": "xmr_heartbeat",
        "engine_version": ENGINE_VERSION,
        "mode": mode,
        "price": price,
        "xmr": xmr_bal,
        "usd": usd_bal,
        "pos_usd": pos_usd,
        "anchor": anchor,
        "gain_pct": gain,
        "slice_usd": slice_usd,
        "swing_high": sh,
        "swing_low": sl,
    })

# ------------------------------------------------------------
# Trade Execution
# ------------------------------------------------------------
def execute_buy(price, state):
    _, usd_bal = get_balances(force=True)

    usd_allowed = get_allocatable_usd(
        asset=ASSET,
        usd_total_available=usd_bal,
        usd_committed_by_asset=0.0,
    )

    if usd_allowed < MIN_USD_BALANCE:
        return False

    usd_to_spend = usd_allowed
    volume = round(usd_to_spend / price, 8)

    res = place_market_order("buy", volume)
    _BAL["ts"] = 0.0

    state["mode"] = "hold"
    state["entry_price"] = price
    state["entry_time"] = time.time()
    state["sell_approach_sent"] = False
    state["last_swing_low"] = price

    tg_send(
        "🟢 XMR BUY EXECUTED\n"
        f"Price: {price:,.2f}\n"
        f"USD Spent: {fmt_usd(usd_to_spend)}\n"
        f"XMR Bought: {volume:.8f}\n"
        f"Engine {ENGINE_VERSION}"
    )

    log_event({
        "event_type": "xmr_buy",
        "engine_version": ENGINE_VERSION,
        "price": price,
        "volume": volume,
        "usd_spent": usd_to_spend,
        "response": res,
    })
    return True

def execute_sell(reason, price, state):
    xmr_bal, _ = get_balances(force=True)

    sell_fraction = get_sell_fraction(ASSET)
    volume = round(xmr_bal * sell_fraction, 8)
    notional = volume * price

    if notional < MIN_USD_BALANCE:
        return False

    res = place_market_order("sell", volume)
    _BAL["ts"] = 0.0

    state["mode"] = "reset"
    state["sell_approach_sent"] = False

    tg_send(
        f"🔵 XMR SELL ({reason})\n"
        f"Price: {price:,.2f}\n"
        f"Sold: {volume:.8f} ({sell_fraction*100:.0f}%)\n"
        f"Credited: {fmt_usd(notional)}\n"
        f"Engine {ENGINE_VERSION}"
    )

    log_event({
        "event_type": f"xmr_sell_{reason}",
        "engine_version": ENGINE_VERSION,
        "price": price,
        "volume": volume,
        "notional": notional,
        "sell_fraction": sell_fraction,
        "response": res,
    })
    return True

# ------------------------------------------------------------
# Engine
# ------------------------------------------------------------
def engine_tick():
    s = load_state()
    price, _ = xmr_price_and_change()
    xmr, usd = get_balances(force=False)

    maybe_send_heartbeat(s, price, xmr, usd)

    pullback = pct(s.get("last_swing_high") or price, price)

    if s["mode"] == "idle":
        if pullback <= BUY_PULLBACK:
            execute_buy(price, s)

    elif s["mode"] == "hold":
        anchor = s.get("last_swing_low") or price
        gain = pct(anchor, price)

        if gain <= DRAWDOWN_RESET:
            execute_sell("drawdown_reset", price, s)
        elif gain >= SELL_TARGET:
            execute_sell("target", price, s)

    elif s["mode"] == "reset":
        if pullback <= BUY_PULLBACK:
            s["mode"] = "idle"

    save_state(s)

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
if __name__ == "__main__":
    try:
        print(f"Kraken XMR Trader {ENGINE_VERSION} tick OK")
        engine_tick()
    except Exception as e:
        msg = str(e)

        if is_rate_limited(msg):
            print(f"[WARN] Kraken rate-limited: {msg}")
            time.sleep(60)
            sys.exit(0)

        tg_send(f"❌ Kraken XMR {ENGINE_VERSION} runtime error:\n{msg}")
        print(f"[ERROR] {msg}")
        time.sleep(30)
        sys.exit(0)
