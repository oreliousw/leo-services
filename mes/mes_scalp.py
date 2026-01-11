#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MES v3.5.99 — PRO Continuation Scalp (Intent Restore) + Rate-Limit Safe Exit
-----------------------------------------------------------------------
• Continuation-only scalp entries (no reversals)
• 4H + 1H structure alignment → single candle check
• Volume restored to informational (not a hard veto)
• Relaxed M1 strong candle threshold (intent restore)
• Clean exit on OANDA rate-limit (prevents systemd FAILED state)
"""

import csv
import fcntl
import json
import logging
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================
# PATHS
# ============================================================
PROJECT_ROOT = Path.home() / "leo-services" / "mes"
PROJECT_ROOT.mkdir(parents=True, exist_ok=True)

MES_DIAG_PATH = PROJECT_ROOT / "latest_diag.json"
TRADE_OBS_PATH = PROJECT_ROOT / "trade_observations.csv"
LOG_PATH = PROJECT_ROOT / "mes.log"
CONFIG_PATH = PROJECT_ROOT / "config.json"

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)

# ============================================================
# SAFE JSON
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
        return super().default(obj)

# ============================================================
# OANDA SESSION
# ============================================================
def _get_oanda_session() -> requests.Session:
    if hasattr(_get_oanda_session, "session"):
        return _get_oanda_session.session
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods={"GET", "POST", "PUT"},
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    _get_oanda_session.session = s
    return s

oanda = _get_oanda_session()

# ============================================================
# CONFIG
# ============================================================
def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}

config = load_config()

OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN", config.get("OANDA_API_KEY", ""))
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", config.get("OANDA_ACCOUNT_ID", ""))
OANDA_REST_URL = os.getenv("OANDA_API_URL", "").rstrip("/")

if not all([OANDA_API_TOKEN, OANDA_ACCOUNT_ID, OANDA_REST_URL]):
    raise RuntimeError("Missing OANDA credentials")

oanda.headers.update({
    "Authorization": f"Bearer {OANDA_API_TOKEN}",
    "Content-Type": "application/json",
})

MODE = "DEMO" if "fxpractice" in OANDA_REST_URL else "LIVE"
VERSION = f"MES v3.5.99 {MODE}"

# ============================================================
# CONSTANTS (INTENT RESTORE)
# ============================================================
INSTRUMENTS = ["EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD", "USD_CAD", "USD_CHF", "EUR_GBP"]
CANDLE_COUNT = 300
ATR_PERIOD = 14

STRONG_CANDLE_ATR_MULT = 0.25   # ← restored intent
PULLBACK_PIPS = 1.5
BUFFER_PIPS = 0.5
MAX_SL_PIPS = 5.0
TP_PIPS = 3.5

DEMO_RISK_PCT = 0.02
LIVE_RISK_PCT = 0.008
RISK_PCT = DEMO_RISK_PCT if MODE == "DEMO" else LIVE_RISK_PCT

# ============================================================
# OANDA HELPERS
# ============================================================
def oanda_get_account_nav() -> float:
    r = oanda.get(f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/summary", timeout=10)
    r.raise_for_status()
    return float(r.json()["account"]["NAV"])

def oanda_get_open_positions() -> Dict[str, float]:
    r = oanda.get(f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/openPositions", timeout=10)
    r.raise_for_status()
    return {
        p["instrument"]: float(p["long"]["units"]) + float(p["short"]["units"])
        for p in r.json().get("positions", [])
    }

def oanda_get_candles(inst: str, tf: str) -> pd.DataFrame:
    r = oanda.get(
        f"{OANDA_REST_URL}/v3/instruments/{inst}/candles",
        params={"granularity": tf, "count": CANDLE_COUNT, "price": "M"},
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
    df = pd.DataFrame(rows).set_index("time")
    if df.empty:
        raise RuntimeError("No candles")
    return df

# ============================================================
# STRUCTURE
# ============================================================
def is_bullish(df): return df.iloc[-1]["close"] > df.iloc[-1]["open"]
def is_bearish(df): return df.iloc[-1]["close"] < df.iloc[-1]["open"]

# ============================================================
# MAIN CYCLE (RATE-LIMIT SAFE)
# ============================================================
def main_cycle():
    logging.info("MES cycle invoked by systemd timer")

    # ---- RATE-LIMIT SAFE NAV FETCH ----
    try:
        nav = oanda_get_account_nav()
    except requests.HTTPError as e:
        msg = str(e).lower()
        if "429" in msg or "rate" in msg:
            logging.warning("OANDA rate-limited — skipping this cycle cleanly")
            return      # <-- CLEAN EXIT (status 0)
        raise

    open_pos = oanda_get_open_positions()

    for inst in INSTRUMENTS:
        df1h = oanda_get_candles(inst, "H1")
        df4h = oanda_get_candles(inst, "H4")

        if not ((is_bullish(df4h) and is_bullish(df1h)) or
                (is_bearish(df4h) and is_bearish(df1h))):
            continue

        df1 = oanda_get_candles(inst, "M1")
        atr = (df1["high"] - df1["low"]).rolling(ATR_PERIOD).mean().iloc[-1]
        body = abs(df1.iloc[-1]["close"] - df1.iloc[-1]["open"])

        if atr <= 0 or body < STRONG_CANDLE_ATR_MULT * atr:
            continue

        logging.info(f"{inst}: continuation conditions met")

    logging.info("MES cycle completed normally")

# ============================================================
# ENTRY POINT (LOCKED)
# ============================================================
if __name__ == "__main__":
    lock_fd = open("/tmp/mes_scalp.lock", "w")
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    try:
        logging.info(f"[MES] {VERSION} starting cycle")
        main_cycle()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
