#!/usr/bin/env python3
import requests
import os
from datetime import datetime

TOKEN = "mro-token"
MINING_TOKEN = os.getenv("MINING_TOKEN")
TELEGRAM_ID = os.getenv("TELEGRAM_ID")

# XMRig stays on native API (works reliably)
API_XMRIG = "http://127.0.0.1:18092/1/summary"

# Dashboard API sources
BASE = "http://127.0.0.1:8080"
API_DAEMON = f"{BASE}/monero"
API_P2POOL = f"{BASE}/p2pool"
API_WALLET = f"{BASE}/wallet"


# ---------- Telegram ----------
def tg(msg):
    if not (MINING_TOKEN and TELEGRAM_ID):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{MINING_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass


# ---------- Helpers ----------
def fetch_json(url, headers=None):
    try:
        r = requests.get(url, headers=headers or {}, timeout=5)
        d = r.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def as_dict(v):  return v if isinstance(v, dict) else {}
def as_list(v):  return v if isinstance(v, list) else []

def atomic_to_xmr(v):
    try:
        return float(v) / 1e12
    except Exception:
        return 0.0


# ---------- MAIN ----------
try:
    # XMRIG (native endpoint)
    miner = as_dict(fetch_json(API_XMRIG, {"Authorization": f"Bearer {TOKEN}"}))

    hr_vals = as_list(as_dict(miner.get("hashrate")).get("total"))
    hashrate = hr_vals[0] if hr_vals else 0

    uptime = miner.get("uptime", 0)

    huge = as_dict(miner.get("huge_pages") or miner.get("hugepages"))
    huge_used = huge.get("used", 0)
    huge_total = huge.get("total", 0)
    huge_pct = huge.get("percentage", 0)

    # NODE
    node = as_dict(fetch_json(API_DAEMON))
    height = node.get("height", "-")
    diff = node.get("difficulty", "-")
    peers_in = node.get("incoming_connections_count", 0)
    peers_out = node.get("outgoing_connections_count", 0)

    # P2POOL
    p2p = as_dict(fetch_json(API_P2POOL))
    p2p_height = p2p.get("height", "-")
    p2p_diff = p2p.get("difficulty", "-")
    p2p_reward = atomic_to_xmr(p2p.get("reward"))
    p2p_peers = p2p.get("peers", "-")

    # WALLET
    wallet = as_dict(fetch_json(API_WALLET))
    balance = atomic_to_xmr(wallet.get("balance"))
    unlocked = atomic_to_xmr(wallet.get("unlocked_balance"))

    rewards = []
    for tx in as_list(wallet.get("in"))[:5]:
        tx = as_dict(tx)
        amt = atomic_to_xmr(tx.get("amount"))
        conf = tx.get("confirmations", 0)
        h = tx.get("height", "-")
        rewards.append(f"{amt:.4f} XMR ({conf} conf @ {h})")

    rewards_text = "\n".join(rewards) if rewards else "None"


    # ---------- REPORT ----------
    msg = f"<b>⛏ Leo Mining Report</b>\n"
    msg += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"

    msg += "<b>⚙️ XMRig Miner</b>\n"
    msg += f"Hashrate: <b>{hashrate:.0f} H/s</b>\n"
    msg += f"Uptime: {uptime // 3600}h {(uptime % 3600)//60}m\n"
    msg += f"Huge Pages: {huge_used}/{huge_total} ({huge_pct}%)\n\n"

    msg += "<b>🟠 Monero Node</b>\n"
    msg += f"Height: {height}\n"
    msg += f"Difficulty: {diff}\n"
    msg += f"Peers: In {peers_in} / Out {peers_out}\n\n"

    msg += "<b>⛓ P2Pool</b>\n"
    msg += f"Height: {p2p_height}\n"
    msg += f"Difficulty: {p2p_diff}\n"
    msg += f"Reward: {p2p_reward:.6f} XMR\n"
    msg += f"Peers: {p2p_peers}\n\n"

    msg += "<b>💰 Wallet</b>\n"
    msg += f"Balance: {balance:.6f} XMR\n"
    msg += f"Unlocked: {unlocked:.6f} XMR\n\n"

    msg += "<b>📥 Recent Rewards</b>\n"
    msg += rewards_text + "\n\n"

    msg += "Running clean 😺"

    tg(msg)

except Exception as e:
    tg(f"<b>⛏ Mining Report Error</b>\n{e}")
