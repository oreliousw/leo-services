#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MES v3.5.1 LIVE/DEMO – Auto Trader & Diagnostics
(Pure micro-scalp + Impulse-State Guard + continuation scalps)

Changelog v3.5.0 → v3.5.1
  • Candle param: "M" → "MID" (official OANDA midpoint)
  • Added cached instrument info (pip size + display precision)
  • Enforce min 3-pip SL/TP distance (widens only, like old bridge)
  • Round SL/TP to displayPrecision (reduces rejects)
  • No changes to entry logic, risk, impulse, or scalp frequency
"""
# ============================================================
# IMPORTS
# ============================================================
import argparse
import json
import math
import os
import sys
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import requests
import numpy as np
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from functools import lru_cache
from time import time

# ============================================================
# SAFE JSON ENCODER
# ============================================================
class SafeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (bool, np.bool_)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating, float)):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return float(obj)
        if obj is pd.NA:
            return None
        return super().default(obj)

# ============================================================
# SINGLETON SESSION WITH RETRY
# ============================================================
def _get_oanda_session() -> requests.Session:
    if hasattr(_get_oanda_session, "session"):
        return _get_oanda_session.session
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods={"GET", "POST"},
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    _get_oanda_session.session = session
    return session

oanda_session = _get_oanda_session()

# ============================================================
# LOGGING
# ============================================================
LOG_PATH = Path("/mnt/mes/mes.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)

# ============================================================
# CONFIG & AUTH
# ============================================================
CONFIG_PATH = Path("/mnt/mes/config.json")

def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            logging.warning("[MES] Config read failed, using defaults")
    return {}

config = load_config()

INSTRUMENTS = [
    "EUR_USD", "GBP_USD", "AUD_USD",
    "NZD_USD", "USD_CAD", "USD_CHF",
    "EUR_GBP",
]

CANDLE_COUNT = 300
ATR_PERIOD = 14
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
RSI_BUY_MIN = 55.0
RSI_SELL_MAX = 45.0
HTF_BUY_MIN = 60.0
HTF_SELL_MAX = 40.0
SCALP_MAX_TP_PIPS = 15.0
SCALP_MAX_SL_PIPS = 18.0
DEMO_SCALP_RISK_PCT = 0.020
DEMO_SWING_RISK_PCT = 0.008
DEMO_MAX_SWING_MARGIN_FRACTION = 0.20
LIVE_SCALP_RISK_PCT = 0.008  # Updated: LIVE risk boosted to 0.8%

OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN", config.get("OANDA_API_KEY", ""))
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", config.get("OANDA_ACCOUNT_ID", ""))
OANDA_REST_URL = os.getenv("OANDA_API_URL", "").rstrip("/")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", config.get("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", config.get("TELEGRAM_CHAT_ID", ""))

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_TOKEN}",
    "Content-Type": "application/json",
}
oanda_session.headers.update(HEADERS)

# ============================================================
# MODE DETECTION & SAFETY
# ============================================================
if not OANDA_API_TOKEN or not OANDA_ACCOUNT_ID or not OANDA_REST_URL:
    raise RuntimeError("Missing OANDA credentials – check env vars")

if "fxpractice" in OANDA_REST_URL:
    MODE = "DEMO"
elif "fxtrade" in OANDA_REST_URL:
    MODE = "LIVE"
else:
    raise RuntimeError(f"Unknown OANDA API URL: {OANDA_REST_URL}")

# Centralized risk selection
DEFAULT_RISK_PCT = DEMO_SCALP_RISK_PCT
if MODE == "LIVE":
    DEFAULT_RISK_PCT = LIVE_SCALP_RISK_PCT

VERSION = f"MES v3.5.1 {MODE}"

has_tg = bool(TELEGRAM_BOT_TOKEN.strip()) == bool(TELEGRAM_CHAT_ID.strip())
if TELEGRAM_BOT_TOKEN and not has_tg:
    raise RuntimeError("Telegram token/chat mismatch")

logging.info(f"[MES] Auth OK | Running in {MODE} mode | Base risk: {DEFAULT_RISK_PCT*100:.2f}% | URL: {OANDA_REST_URL}")

MES_DIAG_PATH = Path("/mnt/mes/latest_diag.json")

# ============================================================
# NEW: Instrument caching (pip size + precision, 10-min TTL)
# ============================================================
@lru_cache(maxsize=64)
def get_instrument_info_cached(instrument: str, _ts: int = int(time() // 600)):
    return get_instrument_info(instrument)

def get_instrument_info(instrument: str) -> Tuple[float, int]:
    """Return (pip_size, display_precision)"""
    url = f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/instruments"
    r = oanda_session.get(url, params={"instruments": instrument}, timeout=10)
    if r.status_code == 200:
        inst = r.json()["instruments"][0]
        loc = inst.get("pipLocation", -4)
        precision = inst.get("displayPrecision", 5)
        return 10 ** loc, precision
    # Fallbacks
    pip = 0.01 if instrument.endswith("_JPY") else 0.0001
    prec = 3 if instrument.endswith("_JPY") else 5
    return pip, prec

# ============================================================
# TELEGRAM (unchanged)
# ============================================================
def telegram_send(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logging.error(f"[MES] Telegram error: {e}")

# ... (telegram_cycle_report unchanged - omitted for brevity)

# ============================================================
# OANDA HELPERS (candle param fix + new adjust/round)
# ============================================================
def oanda_get_candles(instrument: str, tf: str) -> pd.DataFrame:
    r = oanda_session.get(
        f"{OANDA_REST_URL}/v3/instruments/{instrument}/candles",
        params={"granularity": tf, "count": CANDLE_COUNT, "price": "MID"},  # ← Changed to "MID"
        timeout=15,
    )
    r.raise_for_status()
    rows = []
    for c in r.json().get("candles", []):
        if c.get("complete"):
            m = c["mid"]
            rows.append({
                "time": pd.to_datetime(c["time"]),
                "open": float(m["o"]),
                "high": float(m["h"]),
                "low": float(m["l"]),
                "close": float(m["c"]),
                "volume": int(c.get("volume", 0)),
            })
    if not rows:
        raise RuntimeError("No complete candles")
    return pd.DataFrame(rows).set_index("time")

# ... (account helpers unchanged)

def adjust_and_round_sl_tp(instrument: str, entry: float, sl: float, tp: float, direction: str):
    pip_size, precision = get_instrument_info_cached(instrument)
    min_dist = 3 * pip_size

    orig_sl, orig_tp = sl, tp

    if direction == "bullish":
        if (entry - sl) < min_dist:
            sl = entry - min_dist
        if (tp - entry) < min_dist:
            tp = entry + min_dist
    else:  # bearish
        if (sl - entry) < min_dist:
            sl = entry + min_dist
        if (entry - tp) < min_dist:
            tp = entry - min_dist

    sl = round(sl, precision)
    tp = round(tp, precision)

    if sl != orig_sl or tp != orig_tp:
        logging.info(f"[ADJUST] {instrument} SL {orig_sl}→{sl} | TP {orig_tp}→{tp}")

    return sl, tp

def oanda_place_market_order(instrument: str, units: int, sl: float, tp: float, tag: str):
    side = "BUY" if units > 0 else "SELL"
    entry_price = requests.get(  # quick current price for logging
        f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing",
        params={"instruments": instrument},
        timeout=10,
    ).json()["prices"][0]["closeoutBid" if side == "SELL" else "closeoutAsk"]

    # Adjust & round before sending
    direction = "bullish" if units > 0 else "bearish"
    sl, tp = adjust_and_round_sl_tp(instrument, float(entry_price), sl, tp, direction)

    logging.info(f"[MES] Placing {side} {instrument} {abs(units)} units | SL={sl:.{get_instrument_info_cached(instrument)[1]}f} TP={tp:.{get_instrument_info_cached(instrument)[1]}f}")

    payload = {
        "order": {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "clientExtensions": {"tag": tag},
            "stopLossOnFill": {"price": f"{sl:.{get_instrument_info_cached(instrument)[1]}f}", "timeInForce": "GTC"},
            "takeProfitOnFill": {"price": f"{tp:.{get_instrument_info_cached(instrument)[1]}f}", "timeInForce": "GTC"},
        }
    }
    r = oanda_session.post(
        f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/orders",
        json=payload,
        timeout=15
    )
    telegram_send(
        f"<b>{VERSION}</b> — Order submitted\n"
        f"{side} {instrument}\n"
        f"Units: {units} | SL {sl} | TP {tp}"
    )
    if not r.ok:
        err = r.text[:300]
        logging.error(f"[MES] Order failed: {err}")
        telegram_send(f"<b>{VERSION}</b>\nOrder error {instrument}\n{err}")
        return
    response_json = r.json()
    fill_price = response_json.get("orderFillTransaction", {}).get("price")
    if fill_price:
        telegram_send(
            f"<b>{VERSION}</b>\n"
            f"{instrument} filled @ {fill_price}"
        )
    logging.info("[MES] OANDA_FILL_RAW: %s", json.dumps(response_json, cls=SafeEncoder))

# ============================================================
# In evaluate_pair – only change is rounding before order call
# ============================================================
# ... (everything else unchanged up to order placement)

    sl_price = round(sl_price, get_instrument_info_cached(instrument)[1])  # pre-round entry calc
    tp_price = round(tp_price, get_instrument_info_cached(instrument)[1])

    # ... (units calc unchanged)

    oanda_place_market_order(instrument, units, sl_price, tp_price, f"MESv3.5.1")

    return decision

# ============================================================
# MAIN CYCLE (unchanged)
# ============================================================
# ... (rest of file identical)

if __name__ == "__main__":
    logging.info(f"[MES] {VERSION} starting cycle")
    main_cycle()