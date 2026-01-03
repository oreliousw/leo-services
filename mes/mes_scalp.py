#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MES v3.5.95 — PRO Retest Scalp + AI Diagnostics + Clarified Monday Rule
-----------------------------------------------------------------------
• Retest-only scalp entries (no fallback impulse scalps)
• 15m bias + 5m sweep/retest + micro-structure confirmation
• Stops anchored beyond swept high/low + ATR sanity cap
• Fixed 1.9R TP based on actual SL distance
• Correct HTF RSI alignment (1H vs 4H)
• Corrected volume gating (NaN/zero safe)
• ATR + M5 history safety guards
• Demo margin-cap logic restored
• Clear unit semantics (risk_units vs exec_units)
• Session-window discipline (London–NY overlap)
• Retest zone multiplier = 0.7×ATR (evaluation mode)
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
RSI_PERIOD = 14
RSI_BUY_MIN = 55.0
RSI_SELL_MAX = 45.0
HTF_BUY_MIN = 60.0
HTF_SELL_MAX = 40.0

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
VERSION = f"MES v3.5.95 {MODE}"

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
# PRO RETEST CONFIG
# ============================================================
USE_PRO_RETEST_ONLY = True
MIN_RETTEST_LOOKBACK_BARS = 8
RETTEST_ZONE_ATR_MULT = 0.7
MIN_WICK_RATIO_CONFIRM = 0.45
PRO_SL_ATR_CAP_MULT = 1.35
PRO_TP_RR = 1.9
BUFFER_PIPS = 2.5

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

def compute_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    d = series.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    rs = gain.rolling(period).mean() / (loss.rolling(period).mean() + 1e-9)
    return 100 - (100 / (1 + rs))

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

# ============================================================
# RETEST HELPERS
# ============================================================
def find_last_sweep_level(df5: pd.DataFrame, direction: str, lookback: int = MIN_RETTEST_LOOKBACK_BARS):
    if len(df5) < lookback + 4:
        return None, "insufficient_data"
    recent = df5.iloc[-lookback-4:-3]
    if direction == "bullish":
        idx = recent["low"].idxmin()
        lvl = recent.loc[idx, "low"]
        post = df5.loc[idx:].iloc[1:]
        if len(post) < 2 or (post["close"] > lvl).sum() == 0:
            return None, "no_reclaim_after_low_sweep"
        return lvl, "low_sweep"
    else:
        idx = recent["high"].idxmax()
        lvl = recent.loc[idx, "high"]
        post = df5.loc[idx:].iloc[1:]
        if len(post) < 2 or (post["close"] < lvl).sum() == 0:
            return None, "no_reclaim_after_high_sweep"
        return lvl, "high_sweep"

def is_bullish_micro_confirmed(df5: pd.DataFrame, level: float) -> Tuple[bool,str]:
    if len(df5) < 3:
        return False,"none"
    last3 = df5.iloc[-3:]
    if last3["close"].iloc[-1] <= level:
        return False,"close_below"
    if last3["low"].iloc[-1] <= last3["low"].iloc[-2]:
        return False,"no_higher_low"
    bar = last3.iloc[-1]
    body = abs(bar["close"] - bar["open"])
    wick = min(bar["open"], bar["close"]) - bar["low"]
    if wick / (body + 1e-9) >= MIN_WICK_RATIO_CONFIRM:
        return True,"wick_reject"
    return (bar["close"] > bar["open"] and bar["close"] > last3["high"].iloc[-2]),"strong_close"

def is_bearish_micro_confirmed(df5: pd.DataFrame, level: float) -> Tuple[bool,str]:
    if len(df5) < 3:
        return False,"none"
    last3 = df5.iloc[-3:]
    if last3["close"].iloc[-1] >= level:
        return False,"close_above"
    if last3["high"].iloc[-1] >= last3["high"].iloc[-2]:
        return False,"no_lower_high"
    bar = last3.iloc[-1]
    body = abs(bar["close"] - bar["open"])
    wick = bar["high"] - max(bar["open"], bar["close"])
    if wick / (body + 1e-9) >= MIN_WICK_RATIO_CONFIRM:
        return True,"wick_reject"
    return (bar["close"] < bar["open"] and bar["close"] < last3["low"].iloc[-2]),"strong_close"

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
    pip_val_unit = pip if instrument.endswith("USD") else pip / price
    unit_risk = pips * pip_val_unit
    if unit_risk <= 0:
        return 0
    units = int(risk_amt / unit_risk)
    return units if direction == "bullish" else -units

def oanda_place_market_order(instrument, units, sl, tp, tag):
    side = "BUY" if units > 0 else "SELL"
    logging.info(f"[MES] Placing {side} {instrument} {abs(units)} SL={sl} TP={tp}")
    payload = {
        "order": {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "clientExtensions": {"tag": tag},
            "stopLossOnFill": {"price": f"{sl}", "timeInForce": "GTC"},
            "takeProfitOnFill": {"price": f"{tp}", "timeInForce": "GTC"},
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
    sweep_type: str = ""
    confirmation_type: str = ""
    in_zone_ratio: float = 0.0

# ============================================================
# AI-READY TRADE OBS LOG
# ============================================================
OBS_HEADERS = [
    "timestamp_utc","pair","direction",
    "sweep_type","confirmation_type",
    "retest_distance_pips","zone_radius_pips","in_zone_ratio",
    "tp_pips","sl_pips","exec_units","was_margin_capped",
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

    if abs(open_pos.get(instrument, 0)) > 0:
        decision.reasons = ["Existing open position"]
        return decision

    try:
        df1h = oanda_get_candles(instrument, "H1")
        df4h = oanda_get_candles(instrument, "H4")
    except Exception as e:
        decision.reasons = [f"Data fetch failed: {e}"]
        return decision

    # Volume filter
    vol20 = df1h["volume"].rolling(20).mean().iloc[-1]
    curr_vol = df1h["volume"].iloc[-1]
    if pd.isna(vol20) or vol20 <= 0 or curr_vol / vol20 < 0.60:
        decision.reasons = ["Low volume"]
        return decision

    # HTF RSI alignment
    def rsi_htf(v):
        return "bullish" if v >= HTF_BUY_MIN else "bearish" if v <= HTF_SELL_MAX else "neutral"

    rsi_1h = compute_rsi(df1h["close"]).iloc[-1]
    rsi_4h = compute_rsi(df4h["close"]).iloc[-1]
    tf1h = "bullish" if rsi_1h >= RSI_BUY_MIN else "bearish" if rsi_1h <= RSI_SELL_MAX else "neutral"
    tf4h = rsi_htf(rsi_4h)

    if tf1h == "bullish" and tf4h != "bearish":
        direction = "bullish"
    elif tf1h == "bearish" and tf4h != "bullish":
        direction = "bearish"
    else:
        decision.reasons = ["HTF RSI misaligned"]
        return decision

    # M5 environment
    try:
        df5 = oanda_get_candles(instrument, "M5")
    except Exception as e:
        decision.reasons.append(f"M5 data failed: {e}")
        return decision

    if len(df5) < 20:
        decision.reasons.append(f"M5 history too short ({len(df5)} bars)")
        return decision

    sweep_level, sweep_type = find_last_sweep_level(df5, direction)
    if sweep_level is None:
        decision.reasons.append(f"No valid sweep/retest ({sweep_type})")
        return decision

    atr5 = compute_atr(df5, period=14).iloc[-1]
    if pd.isna(atr5) or atr5 <= 0:
        decision.reasons.append("ATR invalid or zero on M5 — skipping")
        return decision

    zone_half = atr5 * RETTEST_ZONE_ATR_MULT
    current = df5["close"].iloc[-1]
    retest_distance = abs(current - sweep_level)
    if retest_distance > zone_half:
        decision.reasons.append("Not in retest zone")
        return decision

    if direction == "bullish":
        confirmed, ctype = is_bullish_micro_confirmed(df5, sweep_level)
    else:
        confirmed, ctype = is_bearish_micro_confirmed(df5, sweep_level)

    if not confirmed:
        decision.reasons.append("5m structure not confirmed")
        return decision

    pip, prec = get_instrument_info_cached(instrument)
    buffer = BUFFER_PIPS * pip

    if direction == "bullish":
        sl_price = sweep_level - buffer
        sl_dist = current - sl_price
    else:
        sl_price = sweep_level + buffer
        sl_dist = sl_price - current

    atr_cap = atr5 * PRO_SL_ATR_CAP_MULT
    if sl_dist > atr_cap:
        sl_dist = atr_cap
        sl_price = current - sl_dist if direction == "bullish" else current + sl_dist

    sl_price = round(sl_price, prec)
    tp_dist = sl_dist * PRO_TP_RR
    tp_price = round(current + tp_dist if direction == "bullish" else current - tp_dist, prec)

    sl_pips = sl_dist / pip
    tp_pips = tp_dist / pip

    risk_units = estimate_units_for_risk(instrument, direction, nav, DEFAULT_RISK_PCT, current, sl_price)

    exec_units = risk_units
    was_margin_capped = False
    if MODE == "DEMO" and abs(risk_units) > 0:
        notional = abs(risk_units) * current
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
    oanda_place_market_order(instrument, exec_units, sl_price, tp_price, "MESv3.5.95_proRetest")

    decision.decision = "BUY" if exec_units > 0 else "SELL"
    decision.direction = direction
    decision.entry_type = "pro_retest_scalp"
    decision.tp_pips = tp_pips
    decision.sl_pips = sl_pips
    decision.risk_units = abs(risk_units)
    decision.exec_units = abs(exec_units)
    decision.was_margin_capped = was_margin_capped
    decision.risk_pct_used = DEFAULT_RISK_PCT
    decision.sweep_type = sweep_type
    decision.confirmation_type = ctype
    decision.in_zone_ratio = (retest_distance / zone_half) if zone_half > 0 else 0.0
    decision.reasons = ["pro_retest_scalp"]

    # Append AI-ready observation
    append_trade_observation({
        "timestamp_utc": ts,
        "pair": instrument,
        "direction": direction,
        "sweep_type": sweep_type,
        "confirmation_type": ctype,
        "retest_distance_pips": round(retest_distance / pip, 2),
        "zone_radius_pips": round(zone_half / pip, 2),
        "in_zone_ratio": round(decision.in_zone_ratio, 3),
        "tp_pips": round(tp_pips, 2),
        "sl_pips": round(sl_pips, 2),
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
                f"| zone {d.in_zone_ratio:.2f}"
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
