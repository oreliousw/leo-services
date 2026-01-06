#!/usr/bin/env python3
# ============================================================
# File: kraken_trader_xmr.py
# Version: v2.6-XMR — Per-Asset USD Slice Autopilot (XMR/USD)
#
# SYSTEM DESIGN PHILOSOPHY — Kraken MES (XMR Line)
#
# Mission:
#   Gradually increase total XMR over time by rotating a limited
#   trading slice through disciplined swing cycles, while the
#   majority of holdings can remain off-exchange as a long-term core.
#
# Core Principles:
#   • TRUST THE RULES — execution is deterministic, not emotional
#   • NO discretionary overrides or manual “review / exit” prompts
#   • Protective logic is explicit and minimal (e.g. −12% reset)
#   • Calm, quiet, disciplined — awareness ≠ intervention
#
# Risk Model:
#   • Core XMR (e.g., on Ledger) is never traded
#   • Only the Kraken slice participates in rotation
#   • Objective = accumulate XMR quantity over time
#
# v2.5-XMR:
#   • LIVE execution via Kraken AddOrder for XMR/USD
#   • BUY: spent entire USD balance; SELL: sold 25% of XMR balance
#
# v2.6-XMR — Per-Asset USD Slice (this version):
#   • Introduces strict usd_slice for XMR strategy:
#       - XMR engine tracks its own usd_slice_xmr (XMR_USD_SLICE_INIT optional)
#       - BUY uses only slice, not global USD pool
#       - SELL credits only this engine’s usd_slice with its own proceeds
#   • Prevents BTC/XMR engines from competing for the same USD
#
# Trading Rules (same thresholds as BTC line):
#   • BUY Trigger:  −3.0% from swing-high
#   • SELL Target:  +5.0% from entry
#   • Near-Zone Alerts:
#       - BUY awareness:  −2.5%
#       - SELL awareness: +4.0%
#   • Safety Reset: Protective reset if unrealized loss ≤ −12% from entry
#   • State Machine: idle → hold → reset
#
# Execution Mode:
#   • LIVE trades — market BUY/SELL on Kraken (no DRY-RUN by default)
#
# Author: Orelious — Kraken MES XMR Line (2026)
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

# Optional: track an off-exchange core XMR reference (e.g., Ledger)
CORE_XMR_REFERENCE = float(os.getenv("CORE_XMR_REFERENCE", "0.0"))

REPORT_HOUR = 6
REPORT_MIN  = 0

# Optional: initial USD slice allocation for XMR engine
XMR_USD_SLICE_INIT = os.getenv("XMR_USD_SLICE_INIT")


# ------------------------------------------------------------
# Trading / Engine Constants
# ------------------------------------------------------------
PAIR = "XMRUSD"             # Kraken asset pair for XMR/USD
MIN_USD_BALANCE = 10.0      # No BUY trades if available USD < this
SELL_FRACTION   = 0.25      # Sell 25% of XMR balance on SELL/reset
DRY_RUN         = False     # Set True if you ever want to simulate only

STATE_FILE = Path("kraken_state_xmr.json")
SNAP_FILE  = Path("portfolio_snapshot_xmr.json")
LOG_FILE   = Path("kraken_events_xmr.jsonl")

# ------------------------------------------------------------
# Logging (AI-ready JSONL)
# ------------------------------------------------------------
def log_event(ev: dict):
    try:
        ev = dict(ev)
        ev.setdefault("timestamp_utc", datetime.utcnow().isoformat() + "Z")
        with LOG_FILE.open("a") as f:
            f.write(json.dumps(ev, default=str) + "\n")
    except Exception as e:
        print(f"[WARN] Log write failed: {e}")


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
    req.add_header("User-Agent", "Kraken-MES-xmr-v2.6")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def place_market_order(side: str, volume: float):
    """
    Place a MARKET order on Kraken.

    side   : "buy" or "sell"
    volume : XMR amount (float)
    """
    volume_str = f"{volume:.8f}"

    if DRY_RUN:
        # Log a simulated order and skip actual exchange call
        log_event({
            "event_type": "dry_run_order",
            "engine_version": "v2.6-XMR",
            "side": side,
            "pair": PAIR,
            "volume": volume_str,
        })
        print(f"[DRY-RUN] Would place {side} order {PAIR} volume={volume_str}")
        return {"result": "dry_run"}

    params = f"pair={PAIR}&type={side}&ordertype=market&volume={volume_str}"
    res = k_private("/0/private/AddOrder", params)
    if res.get("error"):
        raise RuntimeError(f"Kraken AddOrder error: {res['error']}")
    return res


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def pct(a, b):
    return ((b - a) / a) * 100.0 if a else 0.0


def xmr_price_and_change():
    """
    Returns (last_price, 24h_change_pct) for XMR/USD.
    """
    data = k_public("/0/public/Ticker?pair=XMRUSD")
    key = list(data["result"].keys())[0]  # e.g. "XXMRZUSD"
    last = float(data["result"][key]["c"][0])
    open_24h = float(data["result"][key]["o"])
    return last, pct(open_24h, last)


def get_kraken_balances():
    """
    Returns (xmr_slice, usd_balance) for Kraken account.

    NOTE: Assumes all XMR on Kraken is part of the XMR trading slice.
    CORE_XMR_REFERENCE can represent off-exchange core (e.g., Ledger).
    """
    res = k_private("/0/private/Balance", "")
    if res.get("error"):
        raise RuntimeError(res["error"])
    return (
        float(res["result"].get("XXMR", 0.0)),
        float(res["result"].get("ZUSD", 0.0)),
    )


# ------------------------------------------------------------
# Snapshot Report
# ------------------------------------------------------------
def load_snapshot():
    return json.loads(SNAP_FILE.read_text()) if SNAP_FILE.exists() else None


def save_snapshot(v):
    SNAP_FILE.write_text(json.dumps({"last_value": v}, indent=2))


def run_daily_report():
    price, chg24 = xmr_price_and_change()
    xmr_slice, usd_balance = get_kraken_balances()

    slice_value = (xmr_slice * price) + usd_balance
    core_value  = CORE_XMR_REFERENCE * price
    total_value = slice_value + core_value
    total_xmr   = xmr_slice + CORE_XMR_REFERENCE

    SLICE_MIN_THRESHOLD = 0.1
    slice_note = ""
    if xmr_slice < SLICE_MIN_THRESHOLD:
        slice_note = "\n⚠️ XMR slice low — rotation impact may be minimal"

    prev = load_snapshot()
    if prev:
        pl_usd = total_value - prev["last_value"]
        pl_pct = pct(prev["last_value"], total_value)
        pl_line = (
            "Since Yesterday:\n"
            f"• Unrealized P/L: {pl_usd:+.2f} USD ({pl_pct:+.2f}%)"
        )
    else:
        pl_line = "Since Yesterday:\n• Unrealized P/L: — (first snapshot)"

    tg_send(
        "📊 Daily XMR Overview — 6:00 AM\n\n"
        f"XMR Price: ${price:,.2f}\n"
        f"24h Change: {chg24:+.2f}%\n\n"
        "Kraken (XMR Trading Slice):\n"
        f"• XMR: {xmr_slice:.8f}\n"
        f"• USD (total acct): ${usd_balance:,.2f}\n"
        f"• Slice Value: ${slice_value:,.2f}"
        f"{slice_note}\n\n"
        "Core XMR Reference (e.g., Ledger):\n"
        f"• XMR: {CORE_XMR_REFERENCE:.8f}\n"
        f"• Est Value: ${core_value:,.2f}\n\n"
        "Total XMR Exposure:\n"
        f"• XMR: {total_xmr:.8f}\n"
        f"• Est Value: ${total_value:,.2f}\n\n"
        f"{pl_line}\n\n"
        "Mode: XMR Autopilot — v2.6-XMR (market execution, USD slice)"
    )

    save_snapshot(total_value)


def maybe_run_daily_report():
    n = datetime.now()
    if n.hour == REPORT_HOUR and n.minute == REPORT_MIN:
        run_daily_report()


# ------------------------------------------------------------
# Swing Engine — v2.6-XMR Autopilot
# ------------------------------------------------------------
DEFAULT_STATE = {
    "mode": "idle",
    "entry_price": None,
    "last_swing_high": None,
    "buy_approach_sent": False,
    "sell_approach_sent": False,
    "entry_time": None,
    "usd_slice": None,  # per-asset USD envelope for XMR strategy
}


def load_state():
    if STATE_FILE.exists():
        s = json.loads(STATE_FILE.read_text())
    else:
        s = {}

    base = DEFAULT_STATE.copy()
    base.update(s)

    # Initialize usd_slice if missing/None
    if base.get("usd_slice") is None:
        if XMR_USD_SLICE_INIT is not None:
            try:
                base["usd_slice"] = float(XMR_USD_SLICE_INIT)
            except ValueError:
                base["usd_slice"] = 0.0
        else:
            base["usd_slice"] = 0.0

    return base


def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2))


BUY_PULLBACK   = -3.0
BUY_APPROACH   = -2.5
SELL_TARGET    =  5.0
SELL_APPROACH  =  4.0
DRAWDOWN_RESET = -12.0


def get_price():
    price, _ = xmr_price_and_change()
    return price


# ------------------------------------------------------------
# Trade Execution Helpers (XMR, with USD slice)
# ------------------------------------------------------------
def execute_buy(p: float, state: dict):
    """
    Execute BUY using XMR engine's USD slice.
    Only spends up to min(usd_slice, actual USD balance).
    Returns True if trade executed, False otherwise.
    """
    try:
        xmr_slice, usd_balance = get_kraken_balances()
    except Exception as e:
        msg = f"[XMR BUY] Balance fetch failed: {e}"
        print(msg)
        tg_send(f"❌ Kraken XMR v2.6 BUY error (balance):\n{e}")
        log_event({
            "event_type": "xmr_buy_error_balance",
            "engine_version": "v2.6-XMR",
            "error": str(e),
        })
        return False

    slice_before = float(state.get("usd_slice", 0.0))
    usd_available_for_xmr = min(slice_before, usd_balance)

    if usd_available_for_xmr < MIN_USD_BALANCE:
        tg_send(
            "⚠️ XMR BUY signal hit but XMR USD slice is below minimum.\n\n"
            f"USD Slice (XMR): ${slice_before:.2f}\n"
            f"USD Balance (acct): ${usd_balance:.2f}\n"
            f"Min Required: ${MIN_USD_BALANCE:.2f}\n\n"
            "No XMR trade executed."
        )
        log_event({
            "event_type": "xmr_buy_skipped_min_usd_slice",
            "engine_version": "v2.6-XMR",
            "usd_slice_xmr": slice_before,
            "usd_balance_total": usd_balance,
            "min_usd_balance": MIN_USD_BALANCE,
            "price": p,
        })
        return False

    usd_to_spend = usd_available_for_xmr
    volume = round(usd_to_spend / p, 8)
    if volume <= 0:
        tg_send(
            "⚠️ XMR BUY signal hit but computed XMR volume is zero.\n"
            "No trade executed."
        )
        log_event({
            "event_type": "xmr_buy_skipped_zero_volume",
            "engine_version": "v2.6-XMR",
            "usd_to_spend": usd_to_spend,
            "price": p,
        })
        return False

    try:
        res = place_market_order("buy", volume)
    except Exception as e:
        msg = f"[XMR BUY] Order failed: {e}"
        print(msg)
        tg_send(f"❌ Kraken XMR v2.6 BUY order error:\n{e}")
        log_event({
            "event_type": "xmr_buy_error_addorder",
            "engine_version": "v2.6-XMR",
            "error": str(e),
            "volume": volume,
            "usd_to_spend": usd_to_spend,
            "price": p,
        })
        return False

    slice_after = max(0.0, slice_before - usd_to_spend)
    state["usd_slice"] = slice_after

    tg_send(
        "🟢 XMR BUY EXECUTED (Swing Rotation)\n"
        f"Pair: {PAIR}\n"
        f"Price (approx): {p:.2f}\n"
        f"USD Spent (XMR slice): ${usd_to_spend:.2f}\n"
        f"XMR Bought: {volume:.8f}\n\n"
        f"USD Slice (XMR): ${slice_before:.2f} → ${slice_after:.2f}\n\n"
        "Mode: XMR AUTOPILOT — v2.6-XMR (USD slice)"
    )

    log_event({
        "event_type": "xmr_buy_executed",
        "engine_version": "v2.6-XMR",
        "pair": PAIR,
        "side": "buy",
        "price": p,
        "volume": volume,
        "usd_spent": usd_to_spend,
        "usd_slice_before": slice_before,
        "usd_slice_after": slice_after,
        "kraken_response": res,
    })

    state["entry_price"] = p
    state["entry_time"] = time.time()
    state["mode"] = "hold"
    state["sell_approach_sent"] = False
    return True


def execute_sell(reason: str, p: float, state: dict):
    """
    Market-sell SELL_FRACTION of XMR balance.
    Credits USD proceeds into XMR engine's USD slice.

    reason: "target" or "drawdown_reset" (for logging/alerts)
    """
    try:
        xmr_slice, usd_balance = get_kraken_balances()
    except Exception as e:
        msg = f"[XMR SELL-{reason}] Balance fetch failed: {e}"
        print(msg)
        tg_send(f"❌ Kraken XMR v2.6 SELL error (balance, {reason}):\n{e}")
        log_event({
            "event_type": f"xmr_sell_error_balance_{reason}",
            "engine_version": "v2.6-XMR",
            "error": str(e),
        })
        return False

    volume = round(xmr_slice * SELL_FRACTION, 8)
    notional = volume * p

    if volume <= 0 or notional < MIN_USD_BALANCE:
        tg_send(
            f"⚠️ XMR SELL condition hit ({reason}) but position too small.\n\n"
            f"XMR Balance: {xmr_slice:.8f}\n"
            f"Planned Sell (25%): {volume:.8f}\n"
            f"Est Notional: ${notional:.2f}\n"
            f"Min Trade Size: ~${MIN_USD_BALANCE:.2f}\n\n"
            "No XMR trade executed."
        )
        log_event({
            "event_type": f"xmr_sell_skipped_min_size_{reason}",
            "engine_version": "v2.6-XMR",
            "xmr_balance": xmr_slice,
            "volume_planned": volume,
            "price": p,
            "notional": notional,
            "min_usd_balance": MIN_USD_BALANCE,
        })
        return False

    try:
        res = place_market_order("sell", volume)
    except Exception as e:
        msg = f"[XMR SELL-{reason}] Order failed: {e}"
        print(msg)
        tg_send(f"❌ Kraken XMR v2.6 SELL order error ({reason}):\n{e}")
        log_event({
            "event_type": f"xmr_sell_error_addorder_{reason}",
            "engine_version": "v2.6-XMR",
            "error": str(e),
            "volume": volume,
            "price": p,
        })
        return False

    slice_before = float(state.get("usd_slice", 0.0))
    slice_after = slice_before + notional
    state["usd_slice"] = slice_after

    label = "XMR SELL TARGET HIT" if reason == "target" else "XMR PROTECTIVE RESET SELL"

    tg_send(
        f"🔵 {label}\n"
        f"Pair: {PAIR}\n"
        f"Price (approx): {p:.2f}\n"
        f"XMR Sold (25% of balance): {volume:.8f}\n"
        f"Est Notional (credited to XMR slice): ${notional:.2f}\n\n"
        f"USD Slice (XMR): ${slice_before:.2f} → ${slice_after:.2f}\n\n"
        "Mode: XMR AUTOPILOT — v2.6-XMR (USD slice)"
    )

    log_event({
        "event_type": f"xmr_sell_executed_{reason}",
        "engine_version": "v2.6-XMR",
        "pair": PAIR,
        "side": "sell",
        "price": p,
        "volume": volume,
        "notional": notional,
        "xmr_balance_before": xmr_slice,
        "usd_slice_before": slice_before,
        "usd_slice_after": slice_after,
        "kraken_response": res,
    })

    # Engine logic: move to RESET after a SELL / reset cycle
    state["mode"] = "reset"
    return True


# ------------------------------------------------------------
# Engine Tick
# ------------------------------------------------------------
def engine_tick():
    s = load_state()
    p = get_price()

    if s["last_swing_high"] is None:
        s["last_swing_high"] = p

    # Update swing high & reset BUY-approach alert if we make a new high
    if p > s["last_swing_high"]:
        s["last_swing_high"] = p
        s["buy_approach_sent"] = False

    pullback = pct(s["last_swing_high"], p)

    # --- IDLE STATE ---
    if s["mode"] == "idle":

        if not s["buy_approach_sent"] and pullback <= BUY_APPROACH:
            buy_trigger = s["last_swing_high"] * (1 + BUY_PULLBACK / 100)
            tg_send(
                "🟡 Approaching XMR BUY Zone\n"
                f"• Pullback: {pullback:.2f}%\n"
                f"• Swing High: {s['last_swing_high']:.2f}\n"
                f"• Current: {p:.2f}\n"
                f"• Buy Trigger: {buy_trigger:.2f} (-3%)\n\n"
                "Mode: XMR AUTOPILOT — Info Only"
            )
            s["buy_approach_sent"] = True

        if pullback <= BUY_PULLBACK:
            buy_trigger = s["last_swing_high"] * (1 + BUY_PULLBACK / 100)

            # Execute BUY using XMR USD slice
            executed = execute_buy(p, s)

            if executed:
                tg_send(
                    "📥 XMR BUY CYCLE ARMED (Post-Execution Context)\n"
                    f"• Swing High: {s['last_swing_high']:.2f}\n"
                    f"• Buy Trigger: {buy_trigger:.2f} (-3%)\n"
                    f"• Current: {p:.2f}\n"
                    f"• Distance Past Trigger: {(pct(buy_trigger, p)):.2f}%\n\n"
                    "Engine: XMR v2.6-XMR (market execution, USD slice)"
                )
            else:
                # If BUY couldn't execute, stay in IDLE
                s["mode"] = "idle"

    # --- HOLD STATE ---
    elif s["mode"] == "hold":
        if not s["entry_price"]:
            # Safety: if entry lost, revert to idle
            s["mode"] = "idle"
        else:
            gain = pct(s["entry_price"], p)
            sell_target = s["entry_price"] * (1 + SELL_TARGET / 100)

            # Protective Drawdown Reset
            if gain <= DRAWDOWN_RESET:
                hold_days = (time.time() - s["entry_time"]) / 86400.0 if s.get("entry_time") else 0.0
                swing_high = s.get("last_swing_high")
                pullback_at_entry = pct(swing_high, s["entry_price"]) if swing_high else None

                log_event({
                    "event_type": "xmr_drawdown_reset",
                    "rule_id": "xmr_drawdown_reset_12_intraday",
                    "engine_version": "v2.6-XMR",
                    "asset": "XMRUSD",
                    "mode_before": "hold",
                    "entry_price": s["entry_price"],
                    "reset_price": p,
                    "unrealized_loss_pct": gain,
                    "hold_duration_days": round(hold_days, 4),
                    "last_swing_high": swing_high,
                    "pullback_pct_at_entry": pullback_at_entry,
                    "pullback_pct_at_reset": pct(swing_high, p) if swing_high else None,
                    "next_action": "partial_exit_and_wait_for_next_cycle_in_reset_state",
                    "notes_ai_hints": [
                        "evaluate -12% reset threshold vs churn for XMR",
                        "compare drawdown_reset vs SELL_TARGET cycles on XMR",
                        "check -3% entries during large declines"
                    ],
                })

                # Execute a protective SELL of 25% XMR
                execute_sell("drawdown_reset", p, s)

                tg_send(
                    "⚠️ XMR Protective Reset Triggered (−12% Drawdown)\n\n"
                    f"Entry (approx): {s['entry_price']:.2f}\n"
                    f"Reset Price:    {p:.2f}\n"
                    f"Loss:           {gain:.2f}%\n"
                    f"Hold Duration:  {hold_days:.2f} days\n\n"
                    "Status: 25% of trading XMR reduced via reset.\n"
                    "Engine now in RESET state, waiting for the next valid cycle.\n\n"
                    "Mode: XMR AUTOPILOT | Rule: drawdown_reset_12\n"
                    "Engine: v2.6-XMR (market execution, USD slice)"
                )

            else:
                # SELL approach alert
                if not s["sell_approach_sent"] and gain >= SELL_APPROACH:
                    tg_send(
                        "🟣 Approaching XMR SELL Target\n"
                        f"• Gain: {gain:.2f}%\n"
                        f"• Entry: {s['entry_price']:.2f}\n"
                        f"• Current: {p:.2f}\n"
                        f"• Sell Target: {sell_target:.2f} (+5%)\n\n"
                        "Mode: XMR AUTOPILOT — Info Only"
                    )
                    s["sell_approach_sent"] = True

                # SELL target hit → execute SELL of 25% XMR
                if gain >= SELL_TARGET:
                    executed = execute_sell("target", p, s)

                    if executed:
                        tg_send(
                            "🎯 XMR SELL TARGET CYCLE COMPLETE\n"
                            f"Entry (approx): {s['entry_price']:.2f}\n"
                            f"Exit Price:     {p:.2f}\n"
                            f"Gain:           {gain:.2f}%\n"
                            f"Target Price:   {sell_target:.2f} (+5%)\n"
                            f"Distance Above Target: {(pct(sell_target, p)):.2f}%\n\n"
                            "25% of XMR balance sold. Engine now in RESET state,\n"
                            "waiting for the next dip cycle.\n\n"
                            "Engine: XMR v2.6-XMR (market execution, USD slice)"
                        )

    # --- RESET STATE ---
    elif s["mode"] == "reset":
        # RESET → return to IDLE when price has pulled back to BUY zone again
        if pullback <= BUY_PULLBACK:
            s["mode"] = "idle"
            s["buy_approach_sent"] = False
            tg_send(
                "⚙️ XMR Reset complete — new cycle armed\n"
                "Price has pulled back to BUY zone.\n\n"
                "Engine: XMR v2.6-XMR (market execution, USD slice)"
            )

    save_state(s)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
if __name__ == "__main__":
    try:
        maybe_run_daily_report()
        print("Kraken XMR Trader v2.6-XMR tick OK")
        engine_tick()
    except Exception as e:
        tg_send(f"❌ Kraken XMR v2.6-XMR runtime error:\n{e}")
        print(f"[FATAL] {e}")
        sys.exit(1)
