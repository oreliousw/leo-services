#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MES v3.5.96 — PRO Continuation Scalp + AI Diagnostics + Clarified Monday Rule
-----------------------------------------------------------------------
• Continuation-only scalp entries (no reversals)
• 4H + 1H structure alignment (HH/HL vs LH/LL)
• M1 strong candle + limit order into 1.5 pip pullback
• Stops beyond recent M1 swing + 5 pip cap
• Fixed ~3.5 pip TP
• Close open trades if HTF alignment breaks
• Volume gating (NaN/zero safe)
• ATR + M1 history safety guards
• Demo margin-cap logic restored
• Clear unit semantics (risk_units vs exec_units)
• Session-window discipline (London–NY overlap)
• AI-ready CSV diagnostics (1 row per executed trade)
      → ~/leo-services/mes/trade_observations.csv
• Repo-safe paths (everything inside mes/)
• NEW: Explicit weekend skip — **Monday is intentionally included**
"""

import argparse
import json
import math
import os
import sys
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
import pandas as pd
import requests
import numpy as np
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from functools import lru_cache
from time import time
import csv

# ============================================================
# PATHS (repo-safe inside lowercase "mes")
# ============================================================
PROJECT_ROOT = Path.home() / "leo-services" / "mes"
PROJECT_ROOT.mkdir(parents=True, exist_ok=True)

MES_DIAG_PATH = PROJECT_ROOT / "latest_diag.json"
TRADE_OBS_PATH = PROJECT_ROOT / "trade_observations.csv"
LOG_PATH = PROJECT_ROOT / "mes.log"
CONFIG_PATH = PROJECT_ROOT / "config.json"

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

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
# SESSION + LOGGING
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

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)

# ============================================================
# CONFIG & CONSTANTS
# ============================================================
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

DEMO_SCALP_RISK_PCT = 0.020
DEMO_MAX_SWING_MARGIN_FRACTION = 0.20
LIVE_SCALP_RISK_PCT = 0.008

OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN", config.get("OANDA_API_KEY", ""))
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", config.get("OANDA_ACCOUNT_ID", ""))
OANDA_REST_URL = os.getenv("OANDA_API_URL", "").rstrip("/")

FOREX_TOKEN = os.getenv("FOREX_TOKEN", config.get("FOREX_TOKEN", ""))
TELEGRAM_ID = os.getenv("TELEGRAM_ID", config.get("TELEGRAM_ID", ""))

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_TOKEN}",
    "Content-Type": "application/json",
}
oanda_session.headers.update(HEADERS)

if not OANDA_API_TOKEN or not OANDA_ACCOUNT_ID or not OANDA_REST_URL:
    raise RuntimeError("Missing OANDA credentials")

MODE = "DEMO" if "fxpractice" in OANDA_REST_URL else "LIVE"
DEFAULT_RISK_PCT = DEMO_SCALP_RISK_PCT if MODE == "DEMO" else LIVE_SCALP_RISK_PCT
VERSION = f"MES Scalp v3.5.96 {MODE}"

# ============================================================
# TELEGRAM
# ============================================================
def telegram_send(msg: str):
    if not FOREX_TOKEN or not TELEGRAM_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{FOREX_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logging.error(f"[MES] Telegram error: {e}")

# ============================================================
# CONTINUATION CONFIG
# ============================================================
STRONG_CANDLE_ATR_MULT = 0.5
PULLBACK_PIPS = 1.5
BUFFER_PIPS = 0.5
MAX_SL_PIPS = 5.0
TP_PIPS = 3.5
MIN_STRUCTURE_LOOKBACK_BARS = 2

# ============================================================
# SESSION DISCIPLINE (MONDAY INCLUDED)
# ============================================================
ENFORCE_SESSION_WINDOW = True

def is_trading_window() -> bool:
    """
    Trading schedule intent:
    - ✔ Monday through Friday are allowed
    - ✖ Saturday + Sunday are skipped on purpose
    - Friday closes early to avoid illiquid rollover
    """
    now = datetime.now(timezone.utc)
    wd = now.weekday()   # 0=Mon … 6=Sun

    # Explicit weekend skip — Monday IS included
    if wd in (5, 6):
        return False

    # Early cutoff late Friday
    if wd == 4 and now.hour >= 18:
        return False

    # Core London→NY overlap window (approx UTC 13–17)
    return 13 <= now.hour <= 17

# ============================================================
# INDICATORS
# ============================================================
def compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    hi, lo, cl = df["high"], df["low"], df["close"]
    pc = cl.shift(1)
    tr = pd.concat([(hi - lo), (hi - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# ============================================================
# OANDA HELPERS
# ============================================================
def oanda_get_candles(instrument: str, tf: str) -> pd.DataFrame:
    r = oanda_session.get(
        f"{OANDA_REST_URL}/v3/instruments/{instrument}/candles",
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
        raise RuntimeError("No candles returned")
    return df

@lru_cache(maxsize=64)
def get_instrument_info_cached(instrument: str, _ts: int = int(time() // 600)):
    url = f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/instruments"
    r = oanda_session.get(url, params={"instruments": instrument}, timeout=10)
    if r.status_code == 200:
        inst = r.json()["instruments"][0]
        pip = 10 ** inst.get("pipLocation", -4)
        prec = inst.get("displayPrecision", 5)
        return pip, prec
    return (0.0001, 5)

def oanda_get_account_nav() -> float:
    r = oanda_session.get(
        f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/summary",
        timeout=10
    )
    r.raise_for_status()
    return float(r.json()["account"]["NAV"])

def oanda_get_open_positions() -> Dict[str, float]:
    r = oanda_session.get(
        f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/openPositions",
        timeout=10
    )
    r.raise_for_status()
    return {
        p["instrument"]: float(p["long"]["units"]) + float(p["short"]["units"])
        for p in r.json().get("positions", [])
    }

def oanda_close_position(instrument: str, units: float):
    side = "longUnits" if units > 0 else "shortUnits"
    payload = {side: "ALL"}
    r = oanda_session.post(
        f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/positions/{instrument}/close",
        json=payload,
        timeout=15
    )
    logging.info("[MES] OANDA_CLOSE_RAW: %s", json.dumps(r.json(), cls=SafeEncoder))

# ============================================================
# STRUCTURE HELPERS
# ============================================================
def is_bullish_structure(df: pd.DataFrame) -> bool:
    if len(df) < MIN_STRUCTURE_LOOKBACK_BARS:
        return False
    last = df.iloc[-1]
    prev = df.iloc[-2]
    return last["high"] > prev["high"] and last["low"] > prev["low"]

def is_bearish_structure(df: pd.DataFrame) -> bool:
    if len(df) < MIN_STRUCTURE_LOOKBACK_BARS:
        return False
    last = df.iloc[-1]
    prev = df.iloc[-2]
    return last["high"] < prev["high"] and last["low"] < prev["low"]

# ============================================================
# RISK / ORDER HELPERS
# ============================================================
def estimate_units_for_risk(instrument, direction, nav, risk_pct, entry, sl):
    pip, _ = get_instrument_info_cached(instrument)
    risk_amt = nav * risk_pct
    dist = abs(entry - sl)
    if dist <= 0 or pip <= 0:
        return 0
    price = entry if entry > 0 else 1
    pips = dist / pip
    if pips <= 0:
        return 0
    pip_val_unit = pip if instrument.endswith("USD") else pip / price
    unit_risk = pips * pip_val_unit
    if unit_risk <= 0:
        return 0
    units = int(risk_amt / unit_risk)
    return units if direction == "bullish" else -units

def oanda_place_limit_order(instrument, units, entry_price, sl, tp, tag):
    order_type = "BUY" if units > 0 else "SELL"
    logging.info(f"[MES] Placing LIMIT {order_type} {instrument} {abs(units)} @ {entry_price} SL={sl} TP={tp}")
    payload = {
        "order": {
            "type": "LIMIT",
            "instrument": instrument,
            "units": str(units),
            "price": f"{entry_price:.5f}",
            "timeInForce": "GTC",
            "positionFill": "DEFAULT",
            "clientExtensions": {"tag": tag},
            "stopLossOnFill": {"price": f"{sl:.5f}", "timeInForce": "GTC"},
            "takeProfitOnFill": {"price": f"{tp:.5f}", "timeInForce": "GTC"},
        }
    }
    r = oanda_session.post(
        f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/orders",
        json=payload,
        timeout=15
    )
    logging.info("[MES] OANDA_FILL_RAW: %s", json.dumps(r.json(), cls=SafeEncoder))

# ============================================================
# DECISION STRUCT
# ============================================================
@dataclass
class MesDecision:
    pair: str
    time: str
    decision: str
    direction: str
    reasons: List[str]
    entry_type: str = ""
    trade_class: str = "SCALP"
    tp_pips: float = 0.0
    sl_pips: float = 0.0
    risk_units: int = 0
    exec_units: int = 0
    was_margin_capped: bool = False
    risk_pct_used: float = 0.0
    strong_body_ratio: float = 0.0
    pullback_pips: float = 0.0
    in_alignment: bool = True

# ============================================================
# AI-READY TRADE OBS LOG
# ============================================================
OBS_HEADERS = [
    "timestamp_utc","pair","direction",
    "strong_body_ratio","pullback_pips",
    "sl_pips","tp_pips","exec_units","was_margin_capped",
    "session_hour_utc"
]

def append_trade_observation(row: Dict[str, Any]):
    file_exists = TRADE_OBS_PATH.exists()
    with TRADE_OBS_PATH.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OBS_HEADERS)
        if not file_exists:
            w.writeheader()
        w.writerow(row)

# ============================================================
# CORE EVALUATION
# ============================================================
def evaluate_pair(instrument: str, base_risk_pct: float, nav: float, open_pos: Dict[str, float]) -> MesDecision:
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    decision = MesDecision(pair=instrument, time=ts, decision="SKIPPED", direction="NONE", reasons=["Init"])

    # Session discipline
    if ENFORCE_SESSION_WINDOW and not is_trading_window():
        decision.reasons = ["Outside high-quality session"]
        return decision

    try:
        df1h = oanda_get_candles(instrument, "H1")
        df4h = oanda_get_candles(instrument, "H4")
    except Exception as e:
        decision.reasons = [f"Data fetch failed: {e}"]
        return decision

    # Volume filter (kept from original)
    vol20 = df1h["volume"].rolling(20).mean().iloc[-1]
    curr_vol = df1h["volume"].iloc[-1]
    if pd.isna(vol20) or vol20 <= 0 or curr_vol / vol20 < 0.60:
        decision.reasons = ["Low volume"]
        return decision

    # Compute alignment via simple structure (HH/HL vs LH/LL)
    bullish_aligned = is_bullish_structure(df4h) and is_bullish_structure(df1h)
    bearish_aligned = is_bearish_structure(df4h) and is_bearish_structure(df1h)
    if bullish_aligned:
        alignment_dir = "bullish"
    elif bearish_aligned:
        alignment_dir = "bearish"
    else:
        alignment_dir = None

    pos_units = open_pos.get(instrument, 0)
    if pos_units != 0:
        # If open position and alignment breaks → close
        pos_dir = "bullish" if pos_units > 0 else "bearish"
        if alignment_dir != pos_dir:
            oanda_close_position(instrument, pos_units)
            decision.reasons = ["Closed due to alignment break"]
            decision.direction = pos_dir
            decision.in_alignment = False
            return decision
        else:
            decision.reasons = ["Existing position, alignment holds"]
            decision.direction = pos_dir
            return decision

    if alignment_dir is None:
        decision.reasons = ["HTF structure misaligned"]
        return decision

    # M1 environment
    try:
        df1 = oanda_get_candles(instrument, "M1")
    except Exception as e:
        decision.reasons.append(f"M1 data failed: {e}")
        return decision

    if len(df1) < 20:
        decision.reasons.append(f"M1 history too short ({len(df1)} bars)")
        return decision

    atr1 = compute_atr(df1, period=14).iloc[-1]
    if pd.isna(atr1) or atr1 <= 0:
        decision.reasons.append("ATR invalid or zero on M1 — skipping")
        return decision

    last_candle = df1.iloc[-1]
    body = abs(last_candle["close"] - last_candle["open"])
    strong_body_ratio = body / atr1 if atr1 > 0 else 0.0

    is_strong = False
    if alignment_dir == "bullish" and last_candle["close"] > last_candle["open"] and body > STRONG_CANDLE_ATR_MULT * atr1:
        is_strong = True
    elif alignment_dir == "bearish" and last_candle["close"] < last_candle["open"] and body > STRONG_CANDLE_ATR_MULT * atr1:
        is_strong = True

    if not is_strong:
        decision.reasons.append("No strong M1 candle in direction")
        return decision

    pip, prec = get_instrument_info_cached(instrument)
    pullback = PULLBACK_PIPS * pip
    buffer = BUFFER_PIPS * pip
    current = last_candle["close"]
    direction = alignment_dir

    if direction == "bullish":
        entry_price = current - pullback
        recent_swing = min(df1["low"].iloc[-3:])
        sl_price = recent_swing - buffer
        sl_dist = entry_price - sl_price
    else:
        entry_price = current + pullback
        recent_swing = max(df1["high"].iloc[-3:])
        sl_price = recent_swing + buffer
        sl_dist = sl_price - entry_price

    max_sl_dist = MAX_SL_PIPS * pip
    if sl_dist > max_sl_dist:
        sl_dist = max_sl_dist
        sl_price = entry_price - sl_dist if direction == "bullish" else entry_price + sl_dist

    sl_price = round(sl_price, prec)
    tp_dist = TP_PIPS * pip
    tp_price = round(entry_price + tp_dist if direction == "bullish" else entry_price - tp_dist, prec)

    sl_pips = sl_dist / pip
    tp_pips = TP_PIPS

    risk_units = estimate_units_for_risk(instrument, direction, nav, DEFAULT_RISK_PCT, entry_price, sl_price)

    exec_units = risk_units
    was_margin_capped = False
    if MODE == "DEMO" and abs(risk_units) > 0:
        notional = abs(risk_units) * entry_price
        margin_req = notional / 30.0
        max_margin = nav * DEMO_MAX_SWING_MARGIN_FRACTION
        if margin_req > max_margin:
            scale = max_margin / margin_req
            exec_units = int(abs(risk_units) * scale) * (1 if risk_units > 0 else -1)
            was_margin_capped = True
            decision.reasons.append(f"Margin capped {scale:.2%}")

    if exec_units == 0:
        decision.reasons.append("Units=0 after calc")
        return decision

    # Place order
    oanda_place_limit_order(instrument, exec_units, entry_price, sl_price, tp_price, "MESv3.5.96_proContinuation")

    decision.decision = "BUY" if exec_units > 0 else "SELL"
    decision.direction = direction
    decision.entry_type = "pro_continuation_scalp"
    decision.tp_pips = tp_pips
    decision.sl_pips = sl_pips
    decision.risk_units = abs(risk_units)
    decision.exec_units = abs(exec_units)
    decision.was_margin_capped = was_margin_capped
    decision.risk_pct_used = DEFAULT_RISK_PCT
    decision.strong_body_ratio = round(strong_body_ratio, 3)
    decision.pullback_pips = PULLBACK_PIPS
    decision.reasons = ["pro_continuation_scalp"]

    # Append AI-ready observation
    append_trade_observation({
        "timestamp_utc": ts,
        "pair": instrument,
        "direction": direction,
        "strong_body_ratio": decision.strong_body_ratio,
        "pullback_pips": decision.pullback_pips,
        "sl_pips": round(sl_pips, 2),
        "tp_pips": round(tp_pips, 2),
        "exec_units": decision.exec_units,
        "was_margin_capped": was_margin_capped,
        "session_hour_utc": now.hour,
    })

    return decision

# ============================================================
# DIAGNOSTICS + TELEGRAM
# ============================================================
def save_mes_diagnostics(diag_by_pair: Dict[str, MesDecision]):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": VERSION,
        "mode": MODE,
        "pairs": {p: vars(d) for p, d in diag_by_pair.items()},
    }
    MES_DIAG_PATH.write_text(json.dumps(payload, indent=2, cls=SafeEncoder))

def telegram_cycle_report(diag: Dict[str, MesDecision], nav: float, trade_count: int):
    lines = [
        f"<b>{VERSION} – Cycle Summary</b>",
        f"Mode: <b>{MODE}</b> | NAV ≈ ${nav:,.0f}",
        f"Pairs: {len(INSTRUMENTS)} | Orders: <b>{trade_count}</b>",
        "",
    ]
    for pair, d in diag.items():
        lines.append(f"<b>{pair}</b> — {d.decision} | {', '.join(d.reasons)}")
        if d.decision in ("BUY", "SELL"):
            cap = " (CAP)" if d.was_margin_capped else ""
            lines.append(
                f"  ↳ {d.entry_type}{cap} | {d.exec_units}u / {d.risk_units}u "
                f"| SL {d.sl_pips:.1f}p TP {d.tp_pips:.1f}p "
                f"| body {d.strong_body_ratio:.2f} pullback {d.pullback_pips:.1f}p"
            )
    telegram_send("\n".join(lines))

# ============================================================
# MAIN
# ============================================================
def main_cycle():
    nav = oanda_get_account_nav()
    open_pos = oanda_get_open_positions()
    diag: Dict[str, MesDecision] = {}
    trades = 0

    for inst in INSTRUMENTS:
        dec = evaluate_pair(inst, DEFAULT_RISK_PCT, nav, open_pos)
        diag[inst] = dec
        if dec.decision in ("BUY", "SELL"):
            trades += 1

    save_mes_diagnostics(diag)
    telegram_cycle_report(diag, nav, trades)

if __name__ == "__main__":
    logging.info(f"[MES] {VERSION} starting cycle")
    main_cycle()
