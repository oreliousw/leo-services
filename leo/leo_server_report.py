#!/usr/bin/env python3
import requests
import os
from datetime import datetime, timedelta, boot_time
import psutil
import subprocess

LEO_TOKEN = os.getenv("LEO_TOKEN")
TELEGRAM_ID = os.getenv("TELEGRAM_ID")

# ---------- Telegram ----------
def tg(msg):
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
def run_cmd(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else "Error"
    except Exception:
        return "Failed"

def service_status(service_name):
    # Returns "Running" or "Not running" (quiet check)
    code = subprocess.call(["systemctl", "is-active", "--quiet", service_name])
    return "Running" if code == 0 else "Not running"

def get_recent_errors():
    # Last ~1 hour errors/warnings (priority 3=err, 4=warning)
    cmd = ["journalctl", "-p", "3..4", "--since", "1 hour ago", "-n", "10", "--no-pager"]
    output = run_cmd(cmd)
    return output if output else "None"

def get_gpu_info():
    # Optional NVIDIA check via nvidia-smi
    output = run_cmd(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu", "--format=csv,noheader,nounits"])
    if "Error" not in output and output:
        lines = output.split("\n")
        return "\n".join([f"GPU {i}: {temp}°C, {util}% util" for i, line in enumerate(lines) if line.strip()])
    return "No NVIDIA GPU detected"

# ---------- MAIN ----------
try:
    # System basics
    cpu_pct = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    uptime_sec = datetime.now() - datetime.fromtimestamp(boot_time())
    uptime_str = str(timedelta(seconds=int(uptime_sec.total_seconds())))

    # Services (common for Monero/P2Pool/XMRig setup)
    monerod_status = service_status("monerod.service")
    p2pool_status = service_status("p2pool.service")  # Adjust if no .service suffix
    xmrig_status = service_status("xmrig.service")

    # Recent errors
    errors = get_recent_errors()

    # GPU if present
    gpu = get_gpu_info()

    # ---------- REPORT ----------
    msg = f"<b>🖥 Leo Server Health Report</b>\n"
    msg += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"

    msg += "<b>📊 System Stats</b>\n"
    msg += f"CPU: <b>{cpu_pct:.1f}%</b>\n"
    msg += f"Memory: <b>{mem.percent:.1f}%</b> ({mem.used // 1024**3}GB / {mem.total // 1024**3}GB)\n"
    msg += f"Disk (/): <b>{disk.percent:.1f}%</b> ({disk.used // 1024**3}GB / {disk.total // 1024**3}GB)\n"
    msg += f"Uptime: {uptime_str}\n\n"

    if gpu != "No NVIDIA GPU detected":
        msg += "<b>🔥 GPU</b>\n"
        msg += gpu + "\n\n"

    msg += "<b>⚙️ Mining Services</b>\n"
    msg += f"monerod: {monerod_status}\n"
    msg += f"p2pool: {p2pool_status}\n"
    msg += f"xmrig: {xmrig_status}\n\n"

    msg += "<b>⚠️ Recent Errors (last hour)</b>\n"
    msg += errors + "\n\n"

    msg += "All good 😺"
    tg(msg)

except Exception as e:
    tg(f"<b>🖥 Server Report Error</b>\n{e}")