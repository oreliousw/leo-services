#!/usr/bin/env python3
# ------------------------------------------------------------
# Leo Server Report — v1.1.5 (2025-12-28)
#
# Purpose:
#   Hourly Telegram health snapshot for the Leo server
#
# Updates in 1.1.5:
#   - Suppress GPU section when output is invalid or placeholder
#   - Filter out leo_server.service failures from error logs
#   - Minor formatting / readability tuning
# ------------------------------------------------------------

import os
import subprocess
from datetime import datetime, timedelta

import psutil
import requests

# ---------- System uptime ----------
boot_ts = psutil.boot_time()
boot_time = datetime.fromtimestamp(boot_ts)
uptime = datetime.now() - boot_time
uptime_str = str(timedelta(seconds=int(uptime.total_seconds())))

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
    return "🟢 Running" if code == 0 else "🔴 Stopped"


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
    if not output:
        return "None"

    # Filter out noise from THIS service
    lines = [
        ln for ln in output.split("\n")
        if "leo_server.service" not in ln
    ]

    return "\n".join(lines).strip() or "None"


def get_gpu_info() -> str:
    output = run_cmd(
        [
            "nvidia-smi",
            "--query-gpu=temperature.gpu,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
    )

    if not output or "Error" in output or "Failed" in output:
        return ""

    lines = [l.strip() for l in output.split("\n") if l.strip()]
    formatted = []

    for i, row in enumerate(lines):
        parts = [p.strip() for p in row.split(",")]

        if len(parts) == 2:
            temp, util = parts
            formatted.append(f"GPU {i}: {temp}°C, {util}% util")
        elif len(parts) == 1:
            temp = parts[0]
            formatted.append(f"GPU {i}: {temp}°C")
        else:
            formatted.append(f"GPU {i}: {row}")

    return "\n".join(formatted)


# ---------- MAIN ----------
try:
    cpu_pct = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    monerod_status = service_status("monerod.service")
    p2pool_status = service_status("p2pool.service")
    xmrig_status = service_status("xmrig.service")

    errors = get_recent_errors()
    gpu = get_gpu_info()

    msg = (
        "<b>🖥 Leo Server Health Report</b>\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        "<b>📊 System Stats</b>\n"
        f"CPU: <b>{cpu_pct:.1f}%</b>\n"
        f"Memory: <b>{mem.percent:.1f}%</b> "
        f"({mem.used // 1024**3}GB / {mem.total // 1024**3}GB)\n"
        f"Disk (/): <b>{disk.percent:.1f}%</b> "
        f"({disk.used // 1024**3}GB / {disk.total // 1024**3}GB)\n"
        f"Uptime: {uptime_str}\n\n"
    )

    # Only show GPU section if meaningful
    if gpu:
        msg += "<b>🔥 GPU</b>\n" + gpu + "\n\n"

    msg += (
        "<b>⚙️ Mining Services</b>\n"
        f"monerod: {monerod_status}\n"
        f"p2pool: {p2pool_status}\n"
        f"xmrig: {xmrig_status}\n\n"
        "<b>⚠️ Recent Errors (last hour)</b>\n"
        f"{errors}\n\n"
        "All good 😺"
    )

    tg(msg)

except Exception as e:
    tg(f"<b>🖥 Server Report Error</b>\n{e}")
