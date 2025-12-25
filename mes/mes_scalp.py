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
LIVE_SCALP_RISK_PCT = 0.008  # 0.8%

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

DEFAULT_RISK_PCT = DEMO_SCALP_RISK_PCT if MODE == "DEMO" else LIVE_SCALP_RISK_PCT

VERSION = f"MES v3.5.1 {MODE}"

has_tg = bool(TELEGRAM_BOT_TOKEN.strip()) == bool(TELEGRAM_CHAT_ID.strip())
if TELEGRAM_BOT_TOKEN and not has_tg:
    raise RuntimeError("Telegram token/chat mismatch")

logging.info(f"[MES] Auth OK | Running in {MODE} mode | Base risk: {DEFAULT_RISK_PCT*100:.2f}% | URL: {OANDA_REST_URL}")

MES_DIAG_PATH = Path("/mnt/mes/latest_diag.json")

# ============================================================
# INSTRUMENT CACHING (NEW)
# ============================================================
@lru_cache(maxsize=64)
def get_instrument_info_cached(instrument: str, _ts: int = int(time() // 600)) -> Tuple[float, int]:
    return get_instrument_info(instrument)

def get_instrument_info(instrument: str) -> Tuple[float, int]:
    url = f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/instruments"
    r = oanda_session.get(url, params={"instruments": instrument}, timeout=10)
    if r.status_code == 200:
        inst = r.json()["instruments"][0]
        loc = inst.get("pipLocation", -4)
        precision = inst.get("displayPrecision", 5)
        return 10 ** loc, precision
    pip = 0.01 if instrument.endswith("_JPY") else 0.0001
    prec = 3 if instrument.endswith("_JPY") else 5
    return pip, prec

# ============================================================
# TELEGRAM
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

def telegram_cycle_report(diag: Dict[str, "MesDecision"], nav: float, trade_count: int):
    if not has_tg:
        return
    passed_vol = sum(1 for d in diag.values() if "Low volume" not in " ".join(d.reasons))
    passed_htf = sum(1 for d in diag.values() if "HTF RSI alignment" not in " ".join(d.reasons))
    passed_str = sum(1 for d in diag.values() if d.candle_structure != "none")
    passed_macd = sum(1 for d in diag.values() if d.macd_sep is not None and abs(d.macd_sep or 0) >= 1e-5)
    score = (passed_vol + passed_htf + passed_str + passed_macd) / (4 * len(INSTRUMENTS))
    quality = "HIGH" if score >= 0.70 else "MEDIUM" if score >= 0.40 else "LOW"
    lines = [
        f"<b>{VERSION} – Cycle Summary</b>",
        f"Mode: <b>{MODE}</b> | NAV ≈ ${nav:,.0f}",
        f"Pairs evaluated: {len(INSTRUMENTS)} | Orders submitted: <b>{trade_count}</b>",
        f"Market Quality: {quality}",
        "",
        "<b>Per-Pair Snapshot</b>",
    ]
    for pair, dec in diag.items():
        reasons = " | ".join(dec.reasons) if dec.reasons else "None"
        if dec.decision in ("BUY", "SELL"):
            blocker = "Order submitted"
        elif "Existing open position" in reasons:
            blocker = "Open position"
        elif "Low volume" in reasons:
            blocker = "Volume too low"
        elif "HTF RSI alignment" in reasons:
            blocker = "HTF misalignment"
        elif "Impulse" in reasons:
            blocker = "Late entry (impulse spent)"
        elif dec.candle_structure == "none":
            blocker = "No structure"
        elif "margin cap" in " ".join(dec.reasons).lower():
            blocker = "Swing margin capped"
        elif "Units=0" in reasons:
            blocker = "Risk → 0 units"
        else:
            blocker = "Data fetch issue"
        htf = "Bull" if dec.tf_4h == "bullish" else "Bear" if dec.tf_4h == "bearish" else "Neutral"
        notes = f"{htf} 4H • 15M {dec.tf_15m} • {dec.candle_structure or '—'}"
        if dec.macd_sep:
            notes += f" • MACD {'positive' if dec.macd_sep > 0 else 'negative'}"
        lines.append(f"<b>{pair}</b> — {dec.decision} | {blocker}")
        lines.append(f" ↳ {notes}")
        if dec.decision in ("BUY", "SELL"):
            extra = f" ↳ Class: {dec.trade_class} | Risk: {dec.risk_pct_used*100:.2f}%"
            if dec.entry_type:
                extra += f" | <i>{dec.entry_type}</i>"
            if dec.margin_cap_applied:
                extra += f" | Margin capped ({dec.units_scaled:.0f}→{dec.final_units:.0f})"
            lines.append(extra)
        lines.append("")
    telegram_send("\n".join(lines))

# ============================================================
# DECISION STRUCT
# ============================================================
@dataclass
class MesDecision:
    pair: str
    time: str
    decision: str
    direction: str
    atr_trend: str
    macd_sep: Optional[float]
    rsi_15m: Optional[float]
    rsi_1h: Optional[float]
    rsi_4h: Optional[float]
    candle_structure: str
    tf_15m: str
    tf_1h: str
    tf_4h: str
    mode: str
    tp_pips: float
    sl_pips: float
    reasons: List[str]
    trade_class: str = "UNKNOWN"
    risk_pct_used: float = 0.0
    margin_cap_applied: bool = False
    units_scaled: int = 0
    final_units: int = 0
    entry_type: str = ""

# ============================================================
# DIAGNOSTICS
# ============================================================
def save_mes_diagnostics(diag_by_pair: Dict[str, MesDecision]):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": VERSION,
        "trading_mode": MODE,
        "risk_settings": {
            "live_risk_pct": LIVE_SCALP_RISK_PCT,
            "demo_scalp_risk_pct": DEMO_SCALP_RISK_PCT,
            "demo_swing_risk_pct": DEMO_SWING_RISK_PCT,
            "demo_max_swing_margin": DEMO_MAX_SWING_MARGIN_FRACTION,
        },
        "pairs": {p: vars(d) for p, d in diag_by_pair.items()},
    }
    try:
        MES_DIAG_PATH.write_text(json.dumps(payload, indent=2, cls=SafeEncoder))
        logging.info(f"[MES] Diagnostics saved")
    except Exception as e:
        logging.error(f"[MES] Diag write failed: {e}")

# ============================================================
# OANDA HELPERS
# ============================================================
def oanda_get_candles(instrument: str, tf: str) -> pd.DataFrame:
    r = oanda_session.get(
        f"{OANDA_REST_URL}/v3/instruments/{instrument}/candles",
        params={"granularity": tf, "count": CANDLE_COUNT, "price": "M"},  # Updated to "MID"
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
    pos = {}
    for p in r.json().get("positions", []):
        pos[p["instrument"]] = float(p["long"]["units"]) + float(p["short"]["units"])
    return pos

# ============================================================
# NEW: SL/TP Adjustment & Rounding
# ============================================================
def adjust_and_round_sl_tp(instrument: str, entry: float, sl: float, tp: float, direction: str) -> Tuple[float, float]:
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
        logging.info(f"[ADJUST] {instrument} SL {orig_sl:.{precision}f}→{sl:.{precision}f} | TP {orig_tp:.{precision}f}→{tp:.{precision}f}")

    return sl, tp

def oanda_place_market_order(instrument: str, units: int, sl: float, tp: float, tag: str):
    side = "BUY" if units > 0 else "SELL"
    # Quick current price for accurate adjustment
    pricing_resp = oanda_session.get(
        f"{OANDA_REST_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing",
        params={"instruments": instrument},
        timeout=10,
    )
    current_price = float(pricing_resp.json()["prices"][0]["closeoutAsk" if side == "BUY" else "closeoutBid"])

    direction = "bullish" if units > 0 else "bearish"
    sl_adj, tp_adj = adjust_and_round_sl_tp(instrument, current_price, sl, tp, direction)
    precision = get_instrument_info_cached(instrument)[1]

    logging.info(f"[MES] Placing {side} {instrument} {abs(units)} units | SL={sl_adj:.{precision}f} TP={tp_adj:.{precision}f}")

    payload = {
        "order": {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "clientExtensions": {"tag": tag},
            "stopLossOnFill": {"price": f"{sl_adj:.{precision}f}", "timeInForce": "GTC"},
            "takeProfitOnFill": {"price": f"{tp_adj:.{precision}f}", "timeInForce": "GTC"},
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
        f"Units: {units} | SL {sl_adj:.{precision}f} | TP {tp_adj:.{precision}f}"
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
# INDICATORS & STRUCTURE (unchanged)
# ============================================================
def compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    hi, lo, cl = df["high"], df["low"], df["close"]
    pc = cl.shift(1)
    tr = pd.concat([(hi - lo), (hi - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def compute_atr_15m(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hi, lo, cl = df["high"], df["low"], df["close"]
    pc = cl.shift(1)
    tr = pd.concat([(hi - lo), (hi - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def compute_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    rs = gain.rolling(period).mean() / (loss.rolling(period).mean() + 1e-9)
    return 100 - (100 / (1 + rs))

def compute_macd(series: pd.Series):
    e1 = series.ewm(span=MACD_FAST, adjust=False).mean()
    e2 = series.ewm(span=MACD_SLOW, adjust=False).mean()
    macd = e1 - e2
    sig = macd.ewm(span=MACD_SIGNAL, adjust=False).mean()
    return macd, sig, macd - sig

# ============================================================
# IMPULSE STATE – relaxed for continuation scalps (unchanged)
# ============================================================
def impulse_state(df15: pd.DataFrame, df1h: pd.DataFrame, macd_sep: float, vol_ratio: float) -> str:
    lookback = 8
    if len(df15) < lookback + 10:
        return "NONE"
    recent = df15.iloc[-lookback:]
    travel = recent["high"].max() - recent["low"].min()
    atr_val = compute_atr_15m(df15).iloc[-2]
    if atr_val <= 0:
        return "NONE"
    travel_ratio = travel / atr_val
    ranges = (df15["high"] - df15["low"]).iloc[-3:]
    expanding = ranges.is_monotonic_increasing or ranges.iloc[-1] > ranges.mean()
    if travel_ratio < 0.9:
        return "EARLY" if vol_ratio > 1.0 and expanding else "NONE"
    elif 0.9 <= travel_ratio < 1.8 and vol_ratio > 0.8:
        return "ACTIVE"
    elif travel_ratio >= 1.8 or (travel_ratio >= 1.4 and vol_ratio < 0.7):
        if travel_ratio >= 2.2 and vol_ratio < 0.6:
            return "SPENT"
        return "POST_IMPULSE"
    else:
        return "NONE"

def determine_structure(df: pd.DataFrame, direction: str) -> str:
    if df.shape[0] < 6:
        return "none"
    recent = df.iloc[-6:-1]
    last = df.iloc[-1]
    recent_high = recent["high"].max()
    recent_low = recent["low"].min()
    rng = recent_high - recent_low
    if rng <= 0:
        return "none"
    close = last["close"]
    open_ = last["open"]
    hi = last["high"]
    lo = last["low"]
    prev_close = recent["close"].iloc[-1]
    bar_range = hi - lo
    body = close - open_
    body_ratio = abs(body) / bar_range if bar_range > 0 else 0.0
    pos = (close - recent_low) / rng
    MIN_BODY = 0.18
    if direction == "bullish":
        if close > recent_high:
            return "breakout"
        if (pos >= 0.5 and close >= prev_close) or (pos >= 0.6 and body > 0 and body_ratio >= MIN_BODY):
            return "pullback"
        if pos >= 0.55 and body > 0 and body_ratio >= MIN_BODY and close >= prev_close:
            return "continuation"
    else:
        if close < recent_low:
            return "breakout"
        if (pos <= 0.5 and close <= prev_close) or (pos <= 0.4 and body < 0 and body_ratio >= MIN_BODY):
            return "pullback"
        if pos <= 0.45 and body < 0 and body_ratio >= MIN_BODY and close <= prev_close:
            return "continuation"
    return "none"

def estimate_units_for_risk(instrument: str, direction: str, nav: float, risk_pct: float, entry: float, sl: float) -> int:
    risk_amt = nav * risk_pct
    stop_dist = abs(entry - sl)
    pip_size = get_instrument_info_cached(instrument)[0]
    if stop_dist <= 0 or pip_size <= 0:
        return 0
    price = entry if entry > 0 else 1.0
    pips = stop_dist / pip_size
    pip_val_unit = pip_size if instrument.endswith("USD") else pip_size / price
    if pip_val_unit <= 0:
        return 0
    unit_risk = pips * pip_val_unit
    if unit_risk <= 0:
        return 0
    units = int(risk_amt / unit_risk)
    return units if direction == "bullish" else -units

# ============================================================
# PAIR EVALUATION (minor pre-rounding added)
# ============================================================
def evaluate_pair(instrument: str, base_risk_pct: float, nav: float, open_pos: Dict[str, float]) -> MesDecision:
    now = datetime.now(timezone.utc).isoformat()
    decision = MesDecision(
        pair=instrument, time=now, decision="SKIPPED", direction="NONE", atr_trend="unknown",
        macd_sep=None, rsi_15m=None, rsi_1h=None, rsi_4h=None,
        candle_structure="none", tf_15m="neutral", tf_1h="neutral", tf_4h="neutral",
        mode=MODE, tp_pips=0.0, sl_pips=0.0, reasons=["Init"]
    )
    if abs(open_pos.get(instrument, 0)) > 0:
        decision.reasons = ["Existing open position"]
        return decision
    try:
        df15 = oanda_get_candles(instrument, "M15")
        df1h = oanda_get_candles(instrument, "H1")
        df4h = oanda_get_candles(instrument, "H4")
    except Exception as e:
        decision.reasons = [f"Data fetch failed: {e}"]
        return decision
    vol_20 = df1h["volume"].rolling(20).mean().iloc[-1]
    curr_vol = df1h["volume"].iloc[-1]
    vol_ratio = curr_vol / vol_20 if vol_20 > 0 else 0
    if vol_ratio < 0.60:
        decision.reasons = [f"Low volume {vol_ratio:.2f}x avg – skipping"]
        return decision
    atr_series = compute_atr(df1h).dropna()
    atr_val = float(atr_series.iloc[-1]) if len(atr_series) else 0.0
    atr_trend = "rising" if len(atr_series) > 1 and atr_series.iloc[-1] > atr_series.iloc[-2] else "falling"
    macd, sig, _ = compute_macd(df1h["close"])
    macd_sep = float(macd.iloc[-1] - sig.iloc[-1])
    rsi_15m_val = float(compute_rsi(df15["close"]).iloc[-1])
    rsi_1h_val = float(compute_rsi(df1h["close"]).iloc[-1])
    rsi_4h_val = float(compute_rsi(df4h["close"]).iloc[-1])
    def rsi_entry(v): return "bullish" if v >= RSI_BUY_MIN else "bearish" if v <= RSI_SELL_MAX else "neutral"
    def rsi_htf(v): return "bullish" if v >= HTF_BUY_MIN else "bearish" if v <= HTF_SELL_MAX else "neutral"
    tf15 = rsi_entry(rsi_15m_val)
    tf1h = rsi_entry(rsi_1h_val)
    tf4h = rsi_htf(rsi_4h_val)
    if tf1h == "bullish" and tf4h != "bearish":
        direction = "bullish"
    elif tf1h == "bearish" and tf4h != "bullish":
        direction = "bearish"
    else:
        decision.reasons = ["HTF RSI alignment missing (1H vs 4H)"]
        decision.macd_sep = macd_sep
        decision.rsi_15m = rsi_15m_val
        decision.rsi_1h = rsi_1h_val
        decision.rsi_4h = rsi_4h_val
        decision.tf_15m = tf15
        decision.tf_1h = tf1h
        decision.tf_4h = tf4h
        return decision
    structure = determine_structure(df1h, direction)
    if structure == "none":
        decision.reasons = ["No candle structure"]
        decision.candle_structure = structure
        decision.macd_sep = macd_sep
        decision.rsi_15m = rsi_15m_val
        decision.rsi_1h = rsi_1h_val
        decision.rsi_4h = rsi_4h_val
        decision.tf_15m = tf15
        decision.tf_1h = tf1h
        decision.tf_4h = tf4h
        return decision
    impulse = impulse_state(df15, df1h, macd_sep, vol_ratio)
    entry_type = ""
    risk_multiplier = 1.0
    tp_pips_range = (6, 10)
    sl_multiplier = 1.5
    if impulse == "NONE":
        decision.reasons = ["Flat market – no impulse"]
        decision.macd_sep = macd_sep
        decision.rsi_15m = rsi_15m_val
        decision.rsi_1h = rsi_1h_val
        decision.rsi_4h = rsi_4h_val
        decision.tf_15m = tf15
        decision.tf_1h = tf1h
        decision.tf_4h = tf4h
        return decision
    if impulse in ("EARLY", "ACTIVE"):
        entry_type = "Early impulse scalp" if impulse == "EARLY" else "Active impulse scalp"
    elif impulse in ("SPENT", "POST_IMPULSE"):
        htf_aligned = (direction == "bullish" and tf4h != "bearish") or (direction == "bearish" and tf4h != "bullish")
        ltf_confirms = (direction == "bullish" and rsi_15m_val >= RSI_BUY_MIN) or \
                       (direction == "bearish" and rsi_15m_val <= RSI_SELL_MAX)
        if htf_aligned and ltf_confirms and structure != "none":
            entry_type = "Post-impulse continuation (reduced risk)"
            risk_multiplier = 0.55
            tp_pips_range = (5, 8)
            sl_multiplier = 1.0
        else:
            decision.reasons = ["Impulse exhausted – no continuation setup"]
            decision.macd_sep = macd_sep
            decision.rsi_15m = rsi_15m_val
            decision.rsi_1h = rsi_1h_val
            decision.rsi_4h = rsi_4h_val
            decision.tf_15m = tf15
            decision.tf_1h = tf1h
            decision.tf_4h = tf4h
            return decision
    atr_pips = atr_val / get_instrument_info_cached(instrument)[0]
    sl_pips = sl_multiplier * atr_pips
    tp_pips = round(np.random.uniform(*tp_pips_range), 1)
    entry = df1h["close"].iloc[-1]
    sl_price = entry - sl_pips * get_instrument_info_cached(instrument)[0] if direction == "bullish" else entry + sl_pips * get_instrument_info_cached(instrument)[0]
    tp_price = entry + tp_pips * get_instrument_info_cached(instrument)[0] if direction == "bullish" else entry - tp_pips * get_instrument_info_cached(instrument)[0]

    # Minor pre-rounding for safety
    precision = get_instrument_info_cached(instrument)[1]
    sl_price = round(sl_price, precision)
    tp_price = round(tp_price, precision)

    is_swing = tp_pips >= SCALP_MAX_TP_PIPS or sl_pips >= SCALP_MAX_SL_PIPS
    trade_class = "SWING" if is_swing else "SCALP"
    risk_pct_used = base_risk_pct * risk_multiplier if MODE == "DEMO" else base_risk_pct
    units = estimate_units_for_risk(instrument, direction, nav, risk_pct_used, entry, sl_price)
    decision.units_scaled = abs(units)
    margin_cap_applied = False
    if MODE == "DEMO" and trade_class == "SWING" and units != 0:
        price = entry if entry > 0 else df1h["close"].mean()
        notional = abs(units) * price
        margin_required = notional / 30.0
        max_allowed_margin = nav * DEMO_MAX_SWING_MARGIN_FRACTION
        if margin_required > max_allowed_margin:
            scale_factor = max_allowed_margin / margin_required
            units = int(units * scale_factor)
            margin_cap_applied = True
            decision.reasons.append(f"Swing margin cap applied ({scale_factor:.1%})")
    if units == 0:
        decision.reasons = ["Units=0 → risk too high"]
        decision.trade_class = trade_class
        decision.risk_pct_used = risk_pct_used
        decision.tp_pips = tp_pips
        decision.sl_pips = sl_pips
        return decision
    decision.decision = "BUY" if units > 0 else "SELL"
    decision.direction = direction
    decision.atr_trend = atr_trend
    decision.macd_sep = macd_sep
    decision.rsi_15m = rsi_15m_val
    decision.rsi_1h = rsi_1h_val
    decision.rsi_4h = rsi_4h_val
    decision.candle_structure = structure
    decision.tf_15m = tf15
    decision.tf_1h = tf1h
    decision.tf_4h = tf4h
    decision.tp_pips = tp_pips
    decision.sl_pips = sl_pips
    decision.reasons = [entry_type] if entry_type else []
    decision.trade_class = trade_class
    decision.risk_pct_used = risk_pct_used
    decision.margin_cap_applied = margin_cap_applied
    decision.final_units = abs(units)
    decision.entry_type = entry_type
    oanda_place_market_order(instrument, units, sl_price, tp_price, f"MESv3.5.1")
    return decision

# ============================================================
# MAIN CYCLE
# ============================================================
def main_cycle():
    nav = oanda_get_account_nav()
    open_pos = oanda_get_open_positions()
    diag: Dict[str, MesDecision] = {}
    trade_count = 0
    for inst in INSTRUMENTS:
        dec = evaluate_pair(inst, DEFAULT_RISK_PCT, nav, open_pos)
        diag[inst] = dec
        if dec.decision in ("BUY", "SELL"):
            trade_count += 1
    save_mes_diagnostics(diag)
    telegram_cycle_report(diag, nav, trade_count)

if __name__ == "__main__":
    logging.info(f"[MES] {VERSION} starting cycle")
    main_cycle()