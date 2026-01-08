#!/usr/bin/env python3
# ============================================================
# File: kraken_eth.py
# Version: v2.7 — Asset-in-Hand + Balance Summary + Swing-Low PnL (ETH/USD)
#
# Changes in v2.7:
#   • Heartbeat always sends (even if usd_slice=0)
#   • Asset-in-hand detection: if ETH balance > 0 and mode=idle → hold
#   • Track swing LOW while holding to anchor unrealized PnL (Option B)
#   • Heartbeat includes: balances, position value, unrealized PnL ($ and %)
#   • SELL-APPROACH Telegram alert (one-time per leg) at +4% gain
#
# Mode: LIVE — MARKET BUY/SELL (signals-free execution)
# Author: Orelious — Kraken MES ETH Line (2026)
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

ETH_USD_SLICE_INIT = os.getenv("ETH_USD_SLICE_INIT")

# ------------------------------------------------------------
# Trading Constants
# ------------------------------------------------------------
PAIR = "ETHUSD"
MIN_USD_BALANCE = 10.0
SELL_FRACTION   = 0.25
DRY_RUN         = False

STATE_FILE = Path("kraken_state_eth.json")
LOG_FILE   = Path("kraken_events_eth.jsonl")

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
    req.add_header("User-Agent", "Kraken-MES-eth-v2.7")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def place_market_order(side: str, volume: float):
    volume_str = f"{volume:.8f}"

    if DRY_RUN:
        log_event({
            "event_type": "dry_run_order",
            "engine_version": "v2.7",
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

def eth_price_and_change():
    data = k_public("/0/public/Ticker?pair=ETHUSD")
    k = list(data["result"].keys())[0]
    last = float(data["result"][k]["c"][0])
    open_24h = float(data["result"][k]["o"])
    return last, pct(open_24h, last)

def get_kraken_balances():
    res = k_private("/0/private/Balance", "")
    if res.get("error"):
        raise RuntimeError(res["error"])
    return (
        float(res["result"].get("XETH", 0.0)),
        float(res["result"].get("ZUSD", 0.0)),
    )

# ------------------------------------------------------------
# State
# ------------------------------------------------------------
DEFAULT_STATE = {
    "mode": "idle",
    "entry_price": None,          # kept for compatibility, not used as PnL anchor here
    "last_swing_high": None,
    "last_swing_low": None,       # <-- used as PnL anchor in HOLD
    "buy_approach_sent": False,
    "sell_approach_sent": False,  # <-- Telegram gating
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
        if ETH_USD_SLICE_INIT:
            try:
                base["usd_slice"] = float(ETH_USD_SLICE_INIT)
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
# Heartbeat
# ------------------------------------------------------------
HEARTBEAT_INTERVAL_HOURS = 4

def fmt_usd(x):  return f"${x:,.2f}"
def fmt_pct(x):  return f"{x:+.2f}%"

def maybe_send_heartbeat(state, price, eth_bal, usd_bal):
    now = time.time()
    last = state.get("last_heartbeat") or 0
    if now - last < HEARTBEAT_INTERVAL_HOURS * 3600:
        return

    pos_value = eth_bal * price
    anchor = state.get("last_swing_low") or price
    pnl_pct = pct(anchor, price)
    pnl_usd = (price - anchor) * eth_bal

    tg_send(
        "🔎 ETH Heartbeat — v2.7\n"
        f"Mode: {state['mode']}\n"
        f"ETH: {eth_bal:.8f}\n"
        f"USD: {fmt_usd(usd_bal)}\n"
        f"Pos: {fmt_usd(pos_value)} @ {price:,.2f}\n"
        f"PnL (vs swing low {anchor:,.2f}): {fmt_usd(pnl_usd)} ({fmt_pct(pnl_pct)})\n"
        f"Slice: {fmt_usd(float(state.get('usd_slice', 0)))}\n"
        f"Swing H/L: {state.get('last_swing_high', price):,.2f} / {state.get('last_swing_low', price):,.2f}"
    )

    state["last_heartbeat"] = now

# ------------------------------------------------------------
# Asset-in-hand detection + swing tracking
# ------------------------------------------------------------
def ensure_hold_if_asset_present(state, eth_bal, price):
    if eth_bal > 0 and state["mode"] == "idle":
        state["mode"] = "hold"
        state["sell_approach_sent"] = False
        # initialize swing low anchor on first detection
        if state.get("last_swing_low") is None:
            state["last_swing_low"] = price

def update_swing_extremes(state, price):
    if state["last_swing_high"] is None:
        state["last_swing_high"] = price
    if state["last_swing_low"] is None:
        state["last_swing_low"] = price

    if price > state["last_swing_high"]:
        state["last_swing_high"] = price
        state["buy_approach_sent"] = False

    # In HOLD, keep walking the swing low down (PnL anchor B)
    if state["mode"] == "hold" and price < state["last_swing_low"]:
        state["last_swing_low"] = price

# ------------------------------------------------------------
# Trade Execution
# ------------------------------------------------------------
def execute_buy(price, state):
    _, usd_bal = get_kraken_balances()
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

    # reset swing low anchor on new buys
    state["last_swing_low"] = price

    tg_send(
        "🟢 ETH BUY EXECUTED\n"
        f"Price: {price:,.2f}\n"
        f"USD Spent: {fmt_usd(usd_to_spend)}\n"
        f"ETH Bought: {volume:.8f}\n"
        f"USD Slice: {fmt_usd(slice_before)} → {fmt_usd(state['usd_slice'])}\n"
        "Engine v2.7"
    )

    log_event({
        "event_type": "eth_buy",
        "engine_version": "v2.7",
        "price": price,
        "volume": volume,
        "usd_spent": usd_to_spend,
        "slice_before": slice_before,
        "slice_after": state["usd_slice"],
        "response": res,
    })
    return True

def execute_sell(reason, price, state):
    eth_bal, _ = get_kraken_balances()
    volume = round(eth_bal * SELL_FRACTION, 8)
    notional = volume * price
    if notional < MIN_USD_BALANCE:
        return False

    res = place_market_order("sell", volume)

    slice_before = float(state["usd_slice"])
    state["usd_slice"] = slice_before + notional
    state["mode"] = "reset"
    state["sell_approach_sent"] = False

    tg_send(
        f"🔵 ETH SELL ({reason})\n"
        f"Price: {price:,.2f}\n"
        f"Sold: {volume:.8f}\n"
        f"Credited: {fmt_usd(notional)}\n"
        f"USD Slice: {fmt_usd(slice_before)} → {fmt_usd(state['usd_slice'])}\n"
        "Engine v2.7"
    )

    log_event({
        "event_type": f"eth_sell_{reason}",
        "engine_version": "v2.7",
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
    price, _ = eth_price_and_change()
    eth_bal, usd_bal = get_kraken_balances()

    ensure_hold_if_asset_present(s, eth_bal, price)
    update_swing_extremes(s, price)

    # heartbeat always (timed)
    maybe_send_heartbeat(s, price, eth_bal, usd_bal)

    pullback = pct(s["last_swing_high"], price)

    if s["mode"] == "idle":
        if not s["buy_approach_sent"] and pullback <= BUY_APPROACH:
            s["buy_approach_sent"] = True
        if pullback <= BUY_PULLBACK:
            execute_buy(price, s)

    elif s["mode"] == "hold":
        anchor = s.get("last_swing_low") or price
        gain = pct(anchor, price)

        # SELL-APPROACH alert (one-time per leg)
        if (not s.get("sell_approach_sent")) and gain >= SELL_APPROACH and gain < SELL_TARGET:
            s["sell_approach_sent"] = True
            tg_send(
                "⚠️ ETH SELL APPROACH — v2.7\n"
                f"Gain (vs swing low {anchor:,.2f}): {gain:+.2f}%\n"
                f"Price: {price:,.2f}\n"
                f"Target: {SELL_TARGET:.1f}%"
            )

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
        print("Kraken ETH Trader v2.7 tick OK")
        engine_tick()
    except Exception as e:
        tg_send(f"❌ Kraken ETH v2.7 runtime error:\n{e}")
        print(f"[FATAL] {e}")
        sys.exit(1)
