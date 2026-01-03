#!/usr/bin/env python3
# ============================================================
# File: kraken_trader.py
# Version: v2.1 — Awareness & Reporting Upgrade (2026)
#
# SYSTEM DESIGN PHILOSOPHY — Kraken MES (Core + Trading Slice)
#
# Mission:
#   This system is designed to gradually increase total BTC over
#   time by rotating a small trading slice through disciplined
#   swing cycles, while the majority of holdings remain untouched
#   as a long-term core position.
#
# Core Principles:
#   • TRUST THE RULES — execution is automated and deterministic
#   • NO discretionary overrides or human-driven intervention hooks
#   • NO panic logic, fear prompts, or "review / override" events
#   • The system remains calm, quiet, and disciplined by design
#
# Risk Model:
#   • Core BTC is never traded or exposed to rotation risk
#   • Only a limited slice participates in swing accumulation
#   • Objective = grow BTC quantity over time, not chase price action
#
# Behavioral Philosophy:
#   • The system behaves like an autopilot — procedural and predictable
#   • Alerts are minimal and only reflect meaningful cycle events
#     (BUY / SELL / RESET)
#   • Awareness reports provide context — NOT decision prompts
#   • Results are evaluated over time — never mid-cycle
#
# Guiding Intent:
#   Confidence comes from consistency, patience, and discipline.
#   Trust the code. Trust the rules. Let discipline do the work.
#
# Version Direction (v2.1):
#   • Daily 6:00 AM Portfolio Snapshot (Telegram)
#   • BTC price + 24-hour % change
#   • Kraken trading-slice valuation (BTC + USD)
#   • Ledger core BTC reference estimation
#   • Total portfolio valuation
#   • Unrealized P/L vs prior day (USD + %)
#   • Slice-Sanity Awareness line (informational only)
#
# Trading Logic Policy (unchanged by design):
#   • Signals-only swing rotation engine
#   • Trade Slice Concept: ~30% of BTC (exchange-local)
#   • Buy trigger:  −4% pullback from swing-high
#   • Sell trigger: +5% recovery from entry
#   • State machine: idle → hold → reset
#   • NO stop-logic, NO timeout logic, NO override triggers
#
# Execution Mode:
#   • No trades are executed — alerts only
#   • System operates quietly and consistently
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
# Environment / Secrets
# ------------------------------------------------------------
API_KEY_PUBLIC  = os.getenv("KRAKEN_API_KEY")
API_KEY_PRIVATE = os.getenv("KRAKEN_PRV_KEY")
TG_TOKEN = os.getenv("KRAKEN_TOKEN")
TG_CHAT  = os.getenv("TELEGRAM_ID")

if not all([API_KEY_PUBLIC, API_KEY_PRIVATE, TG_TOKEN, TG_CHAT]):
    print("[FATAL] Missing required environment variables.")
    sys.exit(1)


# ------------------------------------------------------------
# User Config
# ------------------------------------------------------------
CORE_BTC_REFERENCE = float(os.getenv("CORE_BTC_REFERENCE", "0.01084"))

REPORT_HOUR = 6
REPORT_MIN  = 0


# ------------------------------------------------------------
# Files
# ------------------------------------------------------------
STATE_FILE = Path("kraken_state.json")
SNAP_FILE  = Path("portfolio_snapshot.json")


# ------------------------------------------------------------
# Telegram Messaging
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
# Kraken API Helpers
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
    req.add_header("User-Agent", "Kraken-MES-v2.1")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


# ------------------------------------------------------------
# Utilities
# ------------------------------------------------------------
def pct(a, b):
    return ((b - a) / a) * 100.0 if a else 0.0


def btc_price_and_change():
    data = k_public("/0/public/Ticker?pair=XBTUSD")
    key = list(data["result"].keys())[0]
    last = float(data["result"][key]["c"][0])
    open_24h = float(data["result"][key]["o"])
    change = pct(open_24h, last)
    return last, change


def get_kraken_balances():
    res = k_private("/0/private/Balance", "")
    if res.get("error"):
        raise RuntimeError(res["error"])
    btc = float(res["result"].get("XXBT", 0.0))
    usd = float(res["result"].get("ZUSD", 0.0))
    return btc, usd


# ------------------------------------------------------------
# Daily Portfolio Snapshot (includes Slice-Sanity Awareness)
# ------------------------------------------------------------
def load_snapshot():
    if SNAP_FILE.exists():
        return json.loads(SNAP_FILE.read_text())
    return None


def save_snapshot(value):
    SNAP_FILE.write_text(json.dumps({"last_value": value}, indent=2))


def run_daily_report():
    price, chg24 = btc_price_and_change()
    btc_slice, usd_slice = get_kraken_balances()

    slice_value = (btc_slice * price) + usd_slice
    core_value  = CORE_BTC_REFERENCE * price
    total_value = slice_value + core_value
    total_btc   = btc_slice + CORE_BTC_REFERENCE

    # ---- Slice-Sanity Awareness (informational only) ----
    SLICE_MIN_THRESHOLD = 0.001
    slice_note = ""
    if btc_slice < SLICE_MIN_THRESHOLD:
        slice_note = "\n⚠️ Slice BTC low — rotation impact may be minimal"

    prev = load_snapshot()
    if prev:
        prev_val = prev["last_value"]
        pl_usd = total_value - prev_val
        pl_pct = pct(prev_val, total_value)
        pl_line = f"Since Yesterday:\n• Unrealized P/L: {pl_usd:+.2f} USD ({pl_pct:+.2f}%)"
    else:
        pl_line = "Since Yesterday:\n• Unrealized P/L: — (first snapshot)"

    msg = (
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
        "Mode: Signals-Only — v2.1"
    )

    tg_send(msg)
    save_snapshot(total_value)


def maybe_run_daily_report():
    now = datetime.now()
    if now.hour == REPORT_HOUR and now.minute == REPORT_MIN:
        run_daily_report()


# ------------------------------------------------------------
# Swing Rotation Engine (unchanged — rule-driven only)
# ------------------------------------------------------------
DEFAULT_STATE = {
    "mode": "idle",
    "entry_price": None,
    "last_swing_high": None
}


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return DEFAULT_STATE.copy()


def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2))


BUY_PULLBACK_PCT = -4.0
SELL_TARGET_PCT  =  5.0


def get_price():
    price, _ = btc_price_and_change()
    return price


def engine_tick():
    state = load_state()
    price = get_price()

    if state["last_swing_high"] is None:
        state["last_swing_high"] = price

    if price > state["last_swing_high"]:
        state["last_swing_high"] = price

    pullback = pct(state["last_swing_high"], price)

    if state["mode"] == "idle":
        if pullback <= BUY_PULLBACK_PCT:
            state["entry_price"] = price
            state["mode"] = "hold"
            tg_send(
                "🟢 BUY SIGNAL (Swing Rotation)\n"
                f"Entry Price: {price}\n"
                f"Pullback: {pullback:.2f}%\n"
                "Trading Slice: 30%"
            )

    elif state["mode"] == "hold":
        gain = pct(state["entry_price"], price)
        if gain >= SELL_TARGET_PCT:
            state["mode"] = "reset"
            tg_send(
                "🔵 SELL SIGNAL (Target Hit)\n"
                f"Entry: {state['entry_price']}\n"
                f"Exit:  {price}\n"
                f"Gain:  {gain:.2f}%\n"
                "Cycle complete — waiting for next dip"
            )

    elif state["mode"] == "reset":
        if pullback <= BUY_PULLBACK_PCT:
            state["mode"] = "idle"
            tg_send("⚙️ Reset complete — new cycle armed")

    save_state(state)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
if __name__ == "__main__":
    try:
        maybe_run_daily_report()
        print("Kraken Trader v2.1 tick OK")
        engine_tick()
    except Exception as e:
        tg_send(f"❌ Kraken v2.1 runtime error:\n{e}")
        print(f"[FATAL] {e}")
        sys.exit(1)
