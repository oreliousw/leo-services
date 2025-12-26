#!/usr/bin/env python3
import requests
import json
from datetime import datetime
import os

# Config
XMRIG_API = "http://127.0.0.1:18092/1/summary"
TOKEN = "mro-token"
TELEGRAM_MINING_TOKEN = os.getenv("TELEGRAM_MINING_TOKEN")
TELEGRAM_MINING_CHAT_ID = os.getenv("TELEGRAM_MINING_CHAT_ID")

def tg(msg):
    if TELEGRAM_MINING_TOKEN and TELEGRAM_MINING_CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_MINING_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_MINING_CHAT_ID, "text": msg, "parse_mode": "HTML"})
        except: pass

try:
    r = requests.get(XMRIG_API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5)
    data = r.json()
    hashrate = data["hashrate"]["total"][0] or 0
    uptime = data["uptime"]
    huge = data["huge_pages"]
    wallet = "Read-only (1Password)"  # or pull from monero-wallet-rpc if you want real balance

    msg = f"<b>⛏ Leo Mining Report</b>\n"
    msg += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    msg += f"Hashrate: <b>{hashrate:.0f} H/s</b>\n"
    msg += f"Uptime: {uptime // 3600}h {(uptime % 3600) // 60}m\n"
    msg += f"Huge Pages: {huge['used']}/{huge['total']} ({huge['percentage']}%)\n"
    msg += f"Wallet: {wallet}\n"
    msg += "Running clean 😺"

    tg(msg)
except Exception as e:
    tg(f"<b>⛏ Mining Report Error</b>\n{e}")