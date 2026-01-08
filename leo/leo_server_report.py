#!/usr/bin/env python3
# ------------------------------------------------------------
# Leo Server Report — v1.2.0 (2026-01-06)
# Purpose: Hourly Telegram health snapshot for Leo server
#
# Changes (v1.2.0):
# • Added Bitcoin Services block
# • bitcoind service status
# • Sync telemetry from bitcoin-cli (IBD, blocks, headers)
# • Safe fallback if bitcoin-cli not installed or node offline
# ------------------------------------------------------------

import os
import socket
import platform
import subprocess
import json
from datetime import datetime, timedelta

import psutil
import requests

# ---------- System uptime ----------
boot_ts = psutil.boot_time()
boot_time = datetime.fromtimestamp(boot_ts)
uptime = datetime.now() - boot_time
uptime_str = str(timedelta(seconds=int(uptime.total_seconds())))

# ---------- Host identity ----------
hostname = socket.gethostname()
kernel = platform.release()
host_line = f"Host: <b>{hostname}</b> • Linux {kernel}"

# ---------- Secrets ----------
LEO_TOKEN = os.getenv("LEO_TOKEN")
TELEGRAM_ID = os.getenv("TELEGRAM_ID")


# ---------- Telegram ----------
def tg(msg: str) -> None:
    if not (LEO_TOKEN and TELEGRAM_ID):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{LEO_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass


# ---------- Helpers ----------
def run_cmd(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else "Error"
    except Exception:
        return "Failed"


def service_status(service_name: str) -> str:
    code = subprocess.call(["systemctl", "is-active", "--quiet", service_name])
    return "🟢 Running" if code == 0 else "🔴 Not running"


def get_recent_errors() -> str:
    cmd = [
        "journalctl",
        "-p",
        "3..4",
        "--since",
        "1 hour ago",
        "-n",
        "10",
        "--no-pager",
    ]
    output = run_cmd(cmd)
    return output if output else "None"


# ---------- Bitcoin sync telemetry ----------
def get_bitcoin_status() -> tuple[str, str, str, str]:
    """
    Returns:
        service status, IBD state, block height, header height
        Falls back to N/A if bitcoin-cli unavailable
    """
    status = service_status("bitcoind.service")

    ibd = "N/A"
    blocks = "N/A"
    headers = "N/A"

    try:
        result = run_cmd(["bitcoin-cli", "getblockchaininfo"])
        if result and result not in ("Error", "Failed"):
            info = json.loads(result)
            ibd = "True" if info.get("initialblockdownload", False) else "False"
            blocks = str(info.get("blocks", 0))
            headers = str(info.get("headers", 0))
    except Exception:
        pass

    return status, ibd, blocks, headers


# ---------- MAIN ----------
try:
    cpu_pct = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    monerod_status = service_status("monerod.service")
    p2pool_status = service_status("p2pool.service")
    xmrig_status = service_status("xmrig.service")

    bitcoind_status, btc_ibd, btc_blocks, btc_headers = get_bitcoin_status()

    errors = get_recent_errors()

    msg = (
        "<b>🖥 Leo Server Health Report</b>\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{host_line}\n\n"
        "<b>📊 System Stats</b>\n"
        f"CPU: <b>{cpu_pct:.1f}%</b>\n"
        f"Memory: <b>{mem.percent:.1f}%</b> "
        f"({mem.used // 1024**3}GB / {mem.total // 1024**3}GB)\n"
        f"Disk (/): <b>{disk.percent:.1f}%</b> "
        f"({disk.used // 1024**3}GB / {disk.total // 1024**3}GB)\n"
        f"Uptime: {uptime_str}\n\n"
        "<b>⚙️ Mining Services</b>\n"
        f"monerod: {monerod_status}\n"
        f"p2pool: {p2pool_status}\n"
        f"xmrig: {xmrig_status}\n\n"
        "<b>₿ Bitcoin Services</b>\n"
        f"bitcoind: {bitcoind_status}\n"
        f"IBD: {btc_ibd}\n"
        f"Blocks: {btc_blocks}\n"
        f"Headers: {btc_headers}\n\n"
        "<b>⚠️ Recent Errors (last hour)</b>\n"
        f"{errors}\n\n"
        "All good 😺"
    )

    tg(msg)

except Exception as e:
    tg(f"<b>🖥 Server Report Error</b>\n{e}")
