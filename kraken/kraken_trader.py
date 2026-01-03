#!/usr/bin/env python3
# ============================================================
# File: kraken_trader.py
# Version: v2.2 — Awareness & Context Signals (2026)
#
# SYSTEM DESIGN PHILOSOPHY — Kraken MES (Core + Trading Slice)
#
# Mission:
#   Gradually increase total BTC over time by rotating a limited
#   trading slice through disciplined swing cycles, while the
#   majority of holdings remain untouched as a long-term core.
#
# Core Principles:
#   • TRUST THE RULES — execution is deterministic, not emotional
#   • NO discretionary overrides or manual “review / exit” prompts
#   • NO stop-logic, panic-logic, or safety overrides
#   • Calm, quiet, disciplined — awareness ≠ intervention
#
# Risk Model:
#   • Core BTC is never traded
#   • Only a defined slice participates in rotation
#   • Objective = accumulate BTC quantity over time
#
# Behavioral Philosophy:
#   • System behaves like an autopilot — procedural + consistent
#   • Alerts reflect meaningful cycle events only
#   • Awareness reporting is informational — not directive
#
# v2.2 Direction:
#   • Adds near-trigger awareness alerts (one per cycle)
#       - BUY pre-zone at −3% (trigger remains −4%)
#       - SELL pre-zone at +4% (target remains +5%)
#   • Adds context-block debugging on BUY + SELL signals
#   • Maintains signals-only mode — no order execution
#
# Trading Rules (fixed):
#   • BUY Trigger:  −4% from swing-high
#   • SELL Target:  +5% from entry
#   • Near-Zone Alerts:
#       - BUY awareness:  −3%
#       - SELL awareness: +4%
#   • State Machine: idle → hold → reset
#
# Execution Mode:
#   • Signals-only — NO trades are placed
#
# Author: Orelious — Kraken MES Crypto Line (2026)
# ============================================================

import os
import sys
import json
import time
import base64
import hmac
import hashlib
import urllib.request
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


CORE_BTC_REFERENCE = float(os.getenv("CORE_BTC_REFERENCE", "0.01084"))
REPORT_HOUR = 6
REPORT_MIN  = 0


STATE_FILE = Path("kraken_state.json")
SNAP_FILE  = Path("portfolio_snapshot.json")


# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------
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
    req.add_header("User-Agent", "Kraken-MES-v2.2")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def pct(a, b):
    return ((b - a) / a) * 100.0 if a else 0.0


def btc_price_and_change():
    data = k_public("/0/public/Ticker?pair=XBTUSD")
    key = list(data["result"].keys())[0]
    last = float(data["result"][key]["c"][0])
    open_24h = float(data["result"][key]["o"])
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
# Snapshot Report (unchanged logic)
# ------------------------------------------------------------
def load_snapshot():
    return json.loads(SNAP_FILE.read_text()) if SNAP_FILE.exists() else None


def save_snapshot(v):
    SNAP_FILE.write_text(json.dumps({"last_value": v}, indent=2))


def run_daily_report():
    price, chg24 = btc_price_and_change()
    btc_slice, usd_slice = get_kraken_balances()

    slice_value = (btc_slice * price) + usd_slice
    core_value  = CORE_BTC_REFERENCE * price
    total_value = slice_value + core_value
    total_btc   = btc_slice + CORE_BTC_REFERENCE

    SLICE_MIN_THRESHOLD = 0.001
    slice_note = ""
    if btc_slice < SLICE_MIN_THRESHOLD:
        slice_note = "\n⚠️ Slice BTC low — rotation impact may be minimal"

    prev = load_snapshot()
    if prev:
        pl_usd = total_value - prev["last_value"]
        pl_pct = pct(prev["last_value"], total_value)
        pl_line = f"Since Yesterday:\n• Unrealized P/L: {pl_usd:+.2f} USD ({pl_pct:+.2f}%)"
    else:
        pl_line = "Since Yesterday:\n• Unrealized P/L: — (first snapshot)"

    tg_send(
        "📊 Daily Crypto Overview — 6:00 AM\n\n"
        f"BTC Price: ${price:,.2f}\n"
        f"24h Change: {chg24:+.2f}%\n\n"
        "Kraken (Trading Slice):\n"
        f"• BTC: {btc_slice:.8f}\n"
        f"• USD: ${usd_slice:,.2f}\n"
        f"• Slice Value: ${slice_value:,.2f}"
        f"{slice_note}\n\n"
        "Ledger Core (reference):\n"
        f"• BTC: {CORE_BTC_REFERENCE:.8f}\n"
        f"• Est Value: ${core_value:,.2f}\n\n"
        "Total Portfolio:\n"
        f"• BTC: {total_btc:.8f}\n"
        f"• Est Value: ${total_value:,.2f}\n\n"
        f"{pl_line}\n\n"
        "Mode: Signals-Only — v2.2"
    )

    save_snapshot(total_value)


def maybe_run_daily_report():
    n = datetime.now()
    if n.hour == REPORT_HOUR and n.minute == REPORT_MIN:
        run_daily_report()


# ------------------------------------------------------------
# Swing Engine — v2.2 Enhancements
# ------------------------------------------------------------
DEFAULT_STATE = {
    "mode": "idle",
    "entry_price": None,
    "last_swing_high": None,
    "buy_approach_sent": False,
    "sell_approach_sent": False,
}


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else DEFAULT_STATE.copy()


def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2))


BUY_PULLBACK = -4.0
BUY_APPROACH = -3.0
SELL_TARGET  =  5.0
SELL_APPROACH = 4.0


def get_price():
    price, _ = btc_price_and_change()
    return price


def engine_tick():
    s = load_state()
    p = get_price()

    if s["last_swing_high"] is None:
        s["last_swing_high"] = p

    if p > s["last_swing_high"]:
        s["last_swing_high"] = p
        s["buy_approach_sent"] = False

    pullback = pct(s["last_swing_high"], p)

    # --- IDLE STATE ---
    if s["mode"] == "idle":

        if not s["buy_approach_sent"] and pullback <= BUY_APPROACH:
            buy_trigger = s["last_swing_high"] * (1 + BUY_PULLBACK / 100)
            tg_send(
                "🟡 Approaching BUY Zone\n"
                f"• Pullback: {pullback:.2f}%\n"
                f"• Swing High: {s['last_swing_high']:.2f}\n"
                f"• Current: {p:.2f}\n"
                f"• Buy Trigger: {buy_trigger:.2f} (-4%)\n\n"
                "Info Only — No Action"
            )
            s["buy_approach_sent"] = True

        if pullback <= BUY_PULLBACK:
            s["entry_price"] = p
            s["mode"] = "hold"
            s["sell_approach_sent"] = False
            buy_trigger = s["last_swing_high"] * (1 + BUY_PULLBACK / 100)
            tg_send(
                "🟢 BUY SIGNAL (Swing Rotation)\n"
                f"Entry Price: {p:.2f}\n"
                f"Pullback: {pullback:.2f}%\n\n"
                "Context:\n"
                f"• Swing High: {s['last_swing_high']:.2f}\n"
                f"• Buy Trigger: {buy_trigger:.2f} (-4%)\n"
                f"• Current: {p:.2f}\n"
                f"• Distance Past Trigger: {(pct(buy_trigger,p)):.2f}%\n\n"
                "Engine: v2.2 (signals-only)"
            )

    # --- HOLD STATE ---
    elif s["mode"] == "hold":
        gain = pct(s["entry_price"], p)
        sell_target = s["entry_price"] * (1 + SELL_TARGET / 100)

        if not s["sell_approach_sent"] and gain >= SELL_APPROACH:
            tg_send(
                "🟣 Approaching SELL Target\n"
                f"• Gain: {gain:.2f}%\n"
                f"• Entry: {s['entry_price']:.2f}\n"
                f"• Current: {p:.2f}\n"
                f"• Sell Target: {sell_target:.2f} (+5%)\n\n"
                "Info Only — No Action"
            )
            s["sell_approach_sent"] = True

        if gain >= SELL_TARGET:
            s["mode"] = "reset"
            tg_send(
                "🔵 SELL SIGNAL (Target Hit)\n"
                f"Entry: {s['entry_price']:.2f}\n"
                f"Exit:  {p:.2f}\n"
                f"Gain:  {gain:.2f}%\n\n"
                "Context:\n"
                f"• Target Price: {sell_target:.2f} (+5%)\n"
                f"• Current: {p:.2f}\n"
                f"• Distance Above Target: {(pct(sell_target,p)):.2f}%\n\n"
                "Cycle Complete — waiting for next dip\n"
                "Engine: v2.2 (signals-only)"
            )

    # --- RESET STATE ---
    elif s["mode"] == "reset":
        if pullback <= BUY_PULLBACK:
            s["mode"] = "idle"
            s["buy_approach_sent"] = False
            tg_send("⚙️ Reset complete — new cycle armed")

    save_state(s)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
if __name__ == "__main__":
    try:
        maybe_run_daily_report()
        print("Kraken Trader v2.2 tick OK")
        engine_tick()
    except Exception as e:
        tg_send(f"❌ Kraken v2.2 runtime error:\n{e}")
        print(f"[FATAL] {e}")
        sys.exit(1)
