#!/usr/bin/env python3
# ============================================================
# Leo ▸ Mining Module
# File: mining.py
#
# Responsibilities:
#   • Monero node health (monerod)
#   • P2Pool status
#   • XMRig miner status
#   • Lightweight operator visibility
#
# Safe by design:
#   • Read-only
#   • No wallet access
#   • No restarts
# ============================================================

import json
from pathlib import Path
from core import run, header, green, yellow, red

# ============================================================
# Services
# ============================================================
SERVICES = {
    "monerod": "monerod.service",
    "p2pool": "p2pool.service",
    "xmrig": "xmrig.service",
}

# ============================================================
# Paths / APIs
# ============================================================
P2POOL_API = Path.home() / ".p2pool" / "api" / "stats_mod"

# ============================================================
# Helpers
# ============================================================
def svc_status(service):
    ok, out = run(
        f"systemctl is-active {service}",
        capture=True
    )
    return out.strip()

def svc_uptime(service):
    ok, out = run(
        f"systemctl show {service} -p ActiveEnterTimestamp",
        capture=True
    )
    if ok and "=" in out:
        return out.split("=", 1)[1]
    return "unknown"

# ============================================================
# Monero Node
# ============================================================
def monerod_status():
    state = svc_status(SERVICES["monerod"])
    if state == "active":
        green(f"✔ monerod running ({svc_uptime(SERVICES['monerod'])})")
    else:
        red(f"✖ monerod status: {state}")

# ============================================================
# P2Pool
# ============================================================
def p2pool_status():
    state = svc_status(SERVICES["p2pool"])
    if state != "active":
        red(f"✖ p2pool status: {state}")
        return

    green(f"✔ p2pool running ({svc_uptime(SERVICES['p2pool'])})")

    if not P2POOL_API.exists():
        yellow("⚠ P2Pool API not found")
        return

    try:
        data = json.loads(P2POOL_API.read_text())
        hr = data.get("hashrate", 0)
        shares = data.get("shares_found", 0)
        print(f"  Hashrate: {hr} H/s")
        print(f"  Shares:   {shares}")
    except Exception as e:
        yellow(f"⚠ Failed to read P2Pool stats: {e}")

# ============================================================
# XMRig
# ============================================================
def xmrig_status():
    state = svc_status(SERVICES["xmrig"])
    if state == "active":
        green(f"✔ xmrig running ({svc_uptime(SERVICES['xmrig'])})")
    else:
        red(f"✖ xmrig status: {state}")

# ============================================================
# Full Summary
# ============================================================
def summary():
    header()
    print("LEO ▸ MINING STATUS\n")

    print("Node:")
    monerod_status()
    print("")

    print("P2Pool:")
    p2pool_status()
    print("")

    print("Miner:")
    xmrig_status()
    print("")

# ============================================================
# Dispatcher
# ============================================================
def handle(args):
    """
    Entry point from leo:
      leo mining
      leo mining status
    """
    if not args or args[0] == "status":
        summary()
        return

    print("""
Usage:
  leo mining
  leo mining status
""")
