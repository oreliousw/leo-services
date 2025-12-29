# Project: Leo Services
# File: mining_telegram_report.py
# Version: v3.5.4 — 2025-12-29
# Change: Huge Pages metric now falls back to parsing XMRig journald logs ("huge pages X/X") instead of /proc smaps.
# Note: Bump Version + Change when modifying runtime behavior

#!/usr/bin/env python3
"""
Leo Mining Telegram Report – v3.5.4
Aligned to P2Pool filesystem API (/home/ubu/.p2pool/api/stats_mod)
Matches dashboard P2Pool fields
"""

import os
import re
import json
import requests
import subprocess
from datetime import datetime, UTC

TOKEN = "mro-token"
MINING_TOKEN = os.getenv("MINING_TOKEN")
TELEGRAM_ID = os.getenv("TELEGRAM_ID")

MONEROD_RPC = "http://127.0.0.1:18081/json_rpc"
WALLET_RPC  = "http://127.0.0.1:18089/json_rpc"

API_XMRIG = "http://127.0.0.1:18092/2/summary"
P2POOL_STATS = "/home/ubu/.p2pool/api/stats_mod"


# ─────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────
def tg(msg: str):
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


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────
def atomic_to_xmr(v):
    try:
        return float(v) / 1e12
    except Exception:
        return 0.0


def as_dict(v):
    return v if isinstance(v, dict) else {}


def as_list(v):
    return v if isinstance(v, list) else []


def rpc_call(url: str, method: str, params=None):
    payload = {"jsonrpc": "2.0", "id": "0", "method": method}
    if params:
        payload["params"] = params
    try:
        r = requests.post(
            url, json=payload,
            headers={"Content-Type": "application/json"},
            timeout=4,
        )
        j = r.json()
        return j.get("result", {}) if isinstance(j, dict) else {}
    except Exception:
        return {}


def load_json_file(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def fmt_hashrate(h):
    try:
        h = float(h)
        if h >= 1e6:  return f"{h/1e6:.2f} MH/s"
        if h >= 1e3:  return f"{h/1e3:.2f} kH/s"
        return f"{h:.0f} H/s"
    except Exception:
        return "N/A"


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
try:
    # XMRIG API
    miner = as_dict(
        requests.get(
            API_XMRIG,
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=4
        ).json()
    )

    hr_vals = as_list(as_dict(miner.get("hashrate")).get("total"))
    hashrate = hr_vals[0] if hr_vals else 0
    uptime = miner.get("uptime", 0)

    # Huge Pages — try API fields first
    huge = as_dict(miner.get("huge_pages") or miner.get("hugepages"))
    huge_used = huge.get("used", 0)
    huge_total = huge.get("total", 0)
    huge_pct = huge.get("percentage", 0)

    # ─────────────────────────────────────────────
    # Fallback: parse XMRig journald logs
    # ─────────────────────────────────────────────
    if not huge_used or not huge_total:
        try:
            out = subprocess.check_output(
                ["journalctl", "-u", "xmrig.service", "-n", "80"],
                text=True
            )
            # Look for lines like:
            # randomx  allocated ... huge pages 100% 3/3 +JIT
            # cpu      READY ... huge pages 100% 8/8 ...
            m = re.findall(r"huge pages\s+\d+%\s+(\d+)/(\d+)", out, re.IGNORECASE)
            if m:
                last = m[-1]  # use the most recent match
                huge_used = int(last[0])
                huge_total = int(last[1])
                huge_pct = round((huge_used / huge_total) * 100, 1) if huge_total else 0
            else:
                huge_used = huge_used or 0
                huge_total = huge_total or 0
                huge_pct = huge_pct or 0
        except Exception:
            huge_used = huge_used or 0
            huge_total = huge_total or 0
            huge_pct = huge_pct or 0

    # MONEROD
    node = as_dict(rpc_call(MONEROD_RPC, "get_info"))
    height = node.get("height", "-")
    diff = node.get("difficulty", "-")
    peers_in = node.get("incoming_connections_count", 0)
    peers_out = node.get("outgoing_connections_count", 0)

    # P2POOL – filesystem API
    p2p = as_dict(load_json_file(P2POOL_STATS))

    net = as_dict(p2p.get("network"))
    pool = as_dict(p2p.get("pool"))
    pstats = as_dict(pool.get("stats"))

    p2p_height = net.get("height", "-")
    p2p_miners = pool.get("miners", "-")
    p2p_hashrate = fmt_hashrate(pool.get("hashrate", 0))
    p2p_round = pool.get("roundHashes", "-")

    last_block = pstats.get("lastBlockFound")
    try:
        ts = int(last_block) / 1000
        last_block_ts = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        last_block_ts = "N/A"

    # WALLET
    wallet = as_dict(rpc_call(WALLET_RPC, "get_balance"))
    balance = atomic_to_xmr(wallet.get("balance", 0))
    unlocked = atomic_to_xmr(wallet.get("unlocked_balance", 0))

    txs = rpc_call(WALLET_RPC, "get_transfers", {"in": True})
    inbound = as_list(txs.get("in")) if isinstance(txs, dict) else as_list(txs)

    rewards = []
    for tx in inbound[-5:]:
        tx = as_dict(tx)
        amt = atomic_to_xmr(tx.get("amount", 0))
        h = tx.get("height", "?")
        rewards.append(f"{amt:.4f} XMR (h={h})")
    rewards_text = "\n".join(rewards) if rewards else "None"

    # ─────────────────────────────────────────────
    # REPORT
    # ─────────────────────────────────────────────
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

    msg += "<b>🏊 P2Pool</b>\n"
    msg += f"Height: {p2p_height}\n"
    msg += f"Miners: {p2p_miners}\n"
    msg += f"Hashrate: {p2p_hashrate}\n"
    msg += f"Round Hashes: {p2p_round}\n"
    msg += f"Last Block: {last_block_ts} UTC\n\n"

    msg += "<b>💰 Wallet</b>\n"
    msg += f"Balance: {balance:.6f} XMR\n"
    msg += f"Unlocked: {unlocked:.6f} XMR\n\n"

    msg += "<b>📥 Recent Rewards</b>\n"
    msg += rewards_text + "\n\n"

    msg += "Running clean 😺"

    tg(msg)

except Exception as e:
    tg(f"<b>⛏ Mining Report Error</b>\n{e}")
