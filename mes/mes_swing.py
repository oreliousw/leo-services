#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MES Swing v1.1.1a – Higher-Timeframe Swing Trader
Enhancement #1: Deterministic 1:2 R:R using 3.0 × ATR TP (random TP removed)
Enhancement #2: Transport-layer reliability – single requests.Session with urllib3 retry + exponential backoff
Location: /opt/mes/swing-v1.1.py
"""

import argparse
import fcntl
import json
import math
import os
import sys
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import numpy as np
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================
# CLI + DUAL-ARM SAFETY
# ============================================================
parser = argparse.ArgumentParser()
parser.add_argument("--allow-live", action="store_true", default=False, help="Required flag to even consider LIVE")
args = parser.parse_args()

ARMED_ENV = os.getenv("MES_SWING_ARMED", "NO").upper() == "YES"
LIVE_ALLOWED = args.allow_live and ARMED_ENV

# ============================================================
# GLOBAL MUTEX – prevents swing + scalper overlap
# ============================================================
LOCK_FILE = Path("/tmp/mes_global.lock")
lock_fd = open(LOCK_FILE, "w")
try:
    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("Another MES process (swing or scalper) is running – exiting")
    sys.exit(1)

# ============================================================
# LOGGING & DIAGNOSTICS
# ============================================================
LOG_DIR = Path("/opt/mes/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "mes_swing.log"
DIAG_PATH = LOG_DIR / "latest_swing_diag.json"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)

# ============================================================
# CONFIG & AUTH
# ============================================================
CONFIG_PATH = Path("/opt/mes/config.json")
def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try: return json.loads(CONFIG_PATH.read_text())
        except Exception as e: logging.warning(f"Config read failed: {e}")
    return {}

config = load_config()

OANDA_API_TOKEN   = os.getenv("OANDA_API_TOKEN", config.get("OANDA_API_KEY", ""))
OANDA_ACCOUNT_ID  = os.getenv("OANDA_ACCOUNT_ID", config.get("OANDA_ACCOUNT_ID", ""))
OANDA_REST_URL    = os.getenv("OANDA_API_URL", "").rstrip("/")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", config.get("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", config.get("TELEGRAM_CHAT_ID", ""))

HEADERS = {"Authorization": f"Bearer {OANDA_API_TOKEN}", "Content-Type": "application/json"}

# ============================================================
# SINGLE SESSION WITH RETRY (v1.1.1a change)
# ============================================================
retry_strategy = Retry(
    total=5,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=None,  # retry on all methods (GET/POST)
    raise_on_status=False,
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session = requests.Session()
session.headers.update(HEADERS)
session.mount("http://", adapter)
session.mount("https://", adapter)

# ============================================================
# MODE DETECTION & FINAL SAFETY GATE
# ============================================================
IS_LIVE = "fxtrade" in OANDA_REST_URL
IS_DEMO = "fxpractice" in OANDA_REST_URL
MODE = "LIVE" if IS_LIVE else "DEMO" if IS_DEMO else "UNKNOWN"

if IS_LIVE and not LIVE_ALLOWED:
    msg = "LIVE account detected but --allow-live flag OR MES_SWING_ARMED=YES missing – refusing to run"
    logging.error(msg)
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": f"SWING SAFETY ABORT: {msg}"},
        timeout=10,
    )
    sys.exit(1)

TAG = "MES_SWING_LIVE_v1" if IS_LIVE else "MES_SWING_DEMO_v1"
VERSION = f"MES Swing v1.1.1a {MODE} [{TAG}]"

# Risk & margin caps
RISK_PCT = float(os.getenv("SWING_RISK_PCT_LIVE", "0.0025")) if IS_LIVE else float(os.getenv("SWING_RISK_PCT_DEMO", "0.02"))
MAX_MARGIN_FRAC = float(os.getenv("SWING_MAX_MARGIN_FRAC_LIVE", "0.10")) if IS_LIVE else float(os.getenv("SWING_MAX_MARGIN_FRAC_DEMO", "0.20"))
MAX_OPEN_POSITIONS = int(os.getenv("SWING_MAX_OPEN_POSITIONS", "2"))
ASSUMED_LEVERAGE = 30

has_tg = bool(TELEGRAM_BOT_TOKEN.strip()) and bool(TELEGRAM_CHAT_ID.strip())
logging.info(f"[SWING] Starting {VERSION} – risk {RISK_PCT:.1%} – margin cap {MAX_MARGIN_FRAC:.0%}")

INSTRUMENTS = [
    "EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD",
    "USD_CAD", "USD_CHF", "EUR_GBP", "USD_JPY",
]

# ============================================================
# TELEGRAM & DIAGNOSTICS
# ============================================================
def tg(msg: str):
    if not has_tg: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logging.error(f"TG error: {e}")

def save_diag(diag: dict):
    try:
        DIAG_PATH.write_text(json.dumps(diag, cls=SafeEncoder, indent=2))
    except: pass

# ============================================================
# OANDA HELPERS – now using shared session with retry
# ============================================================
def oanda_get_candles(instrument: str, tf: str, count: int = 300) -> pd.DataFrame:
    r = session.get(
        f"{OANDA_REST_URL}/v3/instruments/{instrument}/candles",
        params={"granularity": tf, "count": count, "price": "M"},
        timeout=15,
    )
    r.raise_for_status()
    rows = []
    for c in r.json().get("candles", []):
        if c.get("complete"):
            m = c["mid"]
            rows.append({
                "time": pd.to_datetime(c["time"]),
                "open": float(m["o"]), "high": float(m["h"]),
                "low": float(m["l"]), "close": float(m["c"]),
                "volume": int(c.get("volume", 0)),
            })
    df = pd.DataFrame(rows).set_index("time")
    if df.empty: raise ValueError("No complete candles")
    return df

def oanda_get_nav() -> float:
    r = session.get(f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/summary", timeout=10)
    r.raise_for_status()
    return float(r.json()["account"]["NAV"])

def oanda_open_positions() -> Dict[str, float]:
    r = session.get(f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/openPositions", timeout=10)
    r.raise_for_status()
    return {p["instrument"]: float(p["long"]["units"]) + float(p["short"]["units"])
            for p in r.json().get("positions", [])}

def oanda_pip_value(instrument: str) -> float:
    r = session.get(f"{OANDA_REST_URL}/v3/instruments/{instrument}", timeout=10)
    if not r.ok: return 0.0001
    return 10 ** float(r.json()["instrument"]["pipLocation"])

# ============================================================
# INDICATORS
# ============================================================
def atr(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

def rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - 100 / (1 + rs)

# ============================================================
# SWING LOGIC
# ============================================================
@dataclass
class SwingDecision:
    pair: str
    direction: str
    reasons: List[str]
    entry: float
    sl: float
    tp: float
    units_raw: int
    units_final: int
    margin_cap_applied: bool
    risk_pct_used: float

def evaluate_swing(pair: str, nav: float, open_pos: Dict[str, float]) -> Optional[SwingDecision]:
    if abs(open_pos.get(pair, 0)) > 0:
        return None

    try:
        df_d  = oanda_get_candles(pair, "D",  100)
        df_4h = oanda_get_candles(pair, "H4", 200)
        df_1h = oanda_get_candles(pair, "H1", 200)
    except Exception as e:
        tg(f"{pair} data error → skipped")
        return None

    # Daily bias
    daily_rsi = rsi(df_d["close"]).iloc[-1]
    if not (daily_rsi > 55 or daily_rsi < 45):
        return None

    # 4H trend
    ema50_4h = df_4h["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    price_4h = df_4h["close"].iloc[-1]
    trend = "bullish" if price_4h > ema50_4h else "bearish"

    # 1H pullback
    rsi_1h = rsi(df_1h["close"]).iloc[-1]
    if trend == "bullish" and rsi_1h < 45:
        direction = "BUY"
    elif trend == "bearish" and rsi_1h > 55:
        direction = "SELL"
    else:
        return None

    entry = df_1h["close"].iloc[-1]
    atr_val = atr(df_1h)
    pip_size = oanda_pip_value(pair)

    # SL = 1.5 × ATR (unchanged)
    sl_pips = 1.5 * (atr_val / pip_size)
    # TP = 3.0 × ATR → fixed 1:2 risk:reward
    tp_pips = 3.0 * (atr_val / pip_size)

    sl_price = entry - sl_pips * pip_size if direction == "BUY" else entry + sl_pips * pip_size
    tp_price = entry + tp_pips * pip_size if direction == "BUY" else entry - tp_pips * pip_size

    stop_dist = abs(entry - sl_price)
    pips_risk = stop_dist / pip_size
    pip_val = pip_size if pair.endswith("USD") else pip_size / entry

    units_raw = int((nav * RISK_PCT) / (pips_risk * pip_val))
    if units_raw == 0:
        return None

    # Margin cap check
    est_margin = abs(units_raw) * entry / ASSUMED_LEVERAGE
    margin_allowed = nav * MAX_MARGIN_FRAC
    margin_cap_applied = est_margin > margin_allowed
    if margin_cap_applied:
        units_final = int(abs(margin_allowed * ASSUMED_LEVERAGE / entry))
        risk_pct_used = (units_final * pips_risk * pip_val) / nav
    else:
        units_final = units_raw
        risk_pct_used = RISK_PCT

    units_final = units_final if direction == "BUY" else -units_final

    return SwingDecision(
        pair=pair,
        direction=direction,
        reasons=[f"Daily RSI {daily_rsi:.1f}", f"4H {trend}", f"1H RSI {rsi_1h:.1f}"],
        entry=entry,
        sl=round(sl_price, 5),
        tp=round(tp_price, 5),
        units_raw=abs(units_raw),
        units_final=abs(units_final),
        margin_cap_applied=margin_cap_applied,
        risk_pct_used=risk_pct_used,
    )

def place_order(dec: SwingDecision):
    side = "BUY" if dec.units_final > 0 else "SELL"
    payload = {
        "order": {
            "type": "MARKET",
            "instrument": dec.pair,
            "units": str(dec.units_final),
            "timeInForce": "FOK",
            "stopLossOnFill": {"price": f"{dec.sl}"},
            "takeProfitOnFill": {"price": f"{dec.tp}"},
            "clientExtensions": {"tag": TAG}
        }
    }
    r = session.post(
        f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/orders",
        json=payload,
        timeout=15,
    )

    extra = ""
    if dec.margin_cap_applied:
        extra = f"\n<i>Margin cap hit → scaled {dec.units_raw}→{dec.units_final} units ({dec.risk_pct_used:.2%} risk)</i>"

    msg = (f"<b>{VERSION}</b>\n"
           f"{side} {dec.pair} {abs(dec.units_final)} units\n"
           f"Entry ≈ {dec.entry:.5f} | SL {dec.sl} | TP {dec.tp}\n"
           f"Reasons: {' | '.join(dec.reasons)}{extra}")
    tg(msg)

    diag = {
        "timestamp": datetime.utcnow().isoformat(),
        "pair": dec.pair,
        "direction": side,
        "units_raw": dec.units_raw,
        "units_final": dec.units_final,
        "margin_cap_applied": dec.margin_cap_applied,
        "risk_pct_used": dec.risk_pct_used,
        "reasons": dec.reasons,
    }
    save_diag(diag)

    if not r.ok:
        tg(f"Order failed {dec.pair}\n{r.text[:200]}")

    fill = r.json().get("orderFillTransaction", {}).get("price")
    if fill:
        tg(f"{dec.pair} filled @ {fill}")

# ============================================================
# MAIN CYCLE
# ============================================================
def main():
    logging.info(f"[SWING] {VERSION} cycle start")
    tg(f"<b>{VERSION}</b> cycle started")

    nav = oanda_get_nav()
    open_pos = oanda_open_positions()
    current_open = sum(1 for v in open_pos.values() if abs(v) > 0)

    if current_open >= MAX_OPEN_POSITIONS:
        tg(f"<b>{VERSION}</b> already at max open positions ({current_open}) – skipping cycle")
        return

    trades = 0
    for pair in INSTRUMENTS:
        if current_open + trades >= MAX_OPEN_POSITIONS:
            break
        dec = evaluate_swing(pair, nav, open_pos)
        if dec:
            place_order(dec)
            trades += 1
            open_pos[pair] = dec.units_final  # fake occupancy this cycle

    tg(f"<b>{VERSION}</b> cycle complete – {trades} swing order(s) submitted")
    logging.info(f"[SWING] Cycle done – {trades} trades")

if __name__ == "__main__":
    try:
        main()
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
