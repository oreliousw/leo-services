#!/usr/bin/env python3
# ============================================================
# File: kraken_trader.py
# Version: v1.2
# Purpose:
#   Kraken MES Foundation — env-based secrets + Telegram notifier
#
# Secrets (via op run + env file):
#   KRAKEN_API_KEY   = op://<vault>/KRAKEN_API_KEY/api_key
#   KRAKEN_PRV_KEY   = op://<vault>/KRAKEN_PRV_KEY/prv_key
#   KRAKEN_TOKEN     = op://<vault>/KRAKEN_TOKEN/value
#   TELEGRAM_ID      = op://<vault>/TELEGRAM_ID/value
#
# Usage:
#   op run -- python3 kraken_trader.py
#
# This version:
#   - Loads secrets from environment (no op read calls)
#   - Verifies Kraken connectivity
#   - Sends results to Telegram
# ============================================================

import os
import sys
import json
import time
import base64
import hmac
import hashlib
import urllib.request


# ------------------------------------------------------------
# Load secrets from env (expanded by `op run`)
# ------------------------------------------------------------
API_KEY_PUBLIC  = os.getenv("KRAKEN_API_KEY")
API_KEY_PRIVATE = os.getenv("KRAKEN_PRV_KEY")

TG_TOKEN = os.getenv("KRAKEN_TOKEN")
TG_CHAT  = os.getenv("TELEGRAM_ID")

if not all([API_KEY_PUBLIC, API_KEY_PRIVATE, TG_TOKEN, TG_CHAT]):
    print("[FATAL] Missing one or more required environment variables.")
    sys.exit(1)


# ------------------------------------------------------------
# Telegram Notify
# ------------------------------------------------------------
def tg_send(message: str):
    try:
        data = json.dumps({"chat_id": TG_CHAT, "text": message}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req).read()
    except Exception as e:
        print(f"[WARN] Telegram send failed: {e}")


# ------------------------------------------------------------
# Kraken REST Helpers
# ------------------------------------------------------------
API_BASE = "https://api.kraken.com"


def kraken_public(path: str):
    with urllib.request.urlopen(API_BASE + path) as resp:
        return json.loads(resp.read().decode())


def kraken_private(path: str, params: str):
    nonce = str(int(time.time() * 1000))
    post_data = f"nonce={nonce}&{params}"

    sha = hashlib.sha256(nonce.encode() + post_data.encode())
    hmac_digest = hmac.new(
        base64.b64decode(API_KEY_PRIVATE),
        (path.encode() + sha.digest()),
        hashlib.sha512
    )
    signature = base64.b64encode(hmac_digest.digest())

    req = urllib.request.Request(f"{API_BASE}{path}", post_data.encode())
    req.add_header("API-Key", API_KEY_PUBLIC)
    req.add_header("API-Sign", signature)
    req.add_header("User-Agent", "MES-Kraken-v1.2")

    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


# ------------------------------------------------------------
# Smoke Test — Connectivity + Telegram report
# ------------------------------------------------------------
def self_test():
    msg = []

    msg.append("🔐 Kraken Trader v1.2")
    msg.append("Env secrets loaded — OK")

    ticker = kraken_public("/0/public/Ticker?pair=XETHZUSD")
    symbol = list(ticker["result"].keys())[0]
    msg.append(f"🌐 Public API OK — {symbol} ticker retrieved")

    balance = kraken_private("/0/private/Balance", "")
    if balance.get("error"):
        msg.append(f"⚠️ Private API ERROR: {balance['error']}")
    else:
        msg.append("🧾 Private API OK — balance returned")

    summary = "\n".join(msg)
    print(summary)
    tg_send(summary)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
if __name__ == "__main__":
    try:
        self_test()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        tg_send(f"❌ Kraken Trader v1.2 fatal error:\n{e}")
        print(f"[FATAL] {e}")
        sys.exit(1)
