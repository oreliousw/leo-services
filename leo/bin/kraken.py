#!/usr/bin/env python3
# ============================================================
# Leo ▸ Kraken Operator Module
# File: kraken.py
#
# SAFE by design:
#   • No trading
#   • No balance modification
#   • State + systemd control only
# ============================================================

import sys
import json
from pathlib import Path
from core import run, header, confirm, green, yellow, red

# ----------------------------
# Paths / Config
# ----------------------------
HOME = Path.home()
KRAKEN_DIR = HOME / "leo-services" / "kraken"

ASSETS = ["btc", "eth", "xrp", "xmr", "sol"]

# ----------------------------
# Helpers
# ----------------------------
def state_path(asset):
    return KRAKEN_DIR / f"kraken_state_{asset}.json"

def timer_name(asset):
    return f"kraken_{asset}.timer"

def script_path(asset):
    return KRAKEN_DIR / f"kraken_{asset}.py"

def load_state(asset):
    p = state_path(asset)
    if not p.exists():
        return None
    return json.loads(p.read_text())

def save_state(asset, state):
    state_path(asset).write_text(json.dumps(state, indent=2))

# ----------------------------
# Views
# ----------------------------
def overview():
    header()
    print("LEO ▸ KRAKEN OPERATOR\n")
    for a in ASSETS:
        s = load_state(a)
        if not s:
            print(f"{a.upper():4} | NO STATE")
            continue
        mode = s.get("mode", "?").upper()
        slice_ = float(s.get("usd_slice", 0))
        print(f"{a.upper():4} | {mode:6} | Slice ${slice_:,.2f}")
    print("")

def status(asset):
    s = load_state(asset)
    if not s:
        red(f"No state file for {asset.upper()}")
        return

    header()
    print(f"{asset.upper()} STATUS\n" + "-" * 40)
    for k in (
        "mode",
        "usd_slice",
        "last_swing_low",
        "last_swing_high",
        "sell_approach_sent",
        "last_heartbeat",
    ):
        print(f"{k:20}: {s.get(k)}")
    print("")

# ----------------------------
# Actions (guarded)
# ----------------------------
def reset_anchor(asset):
    s = load_state(asset)
    if not s:
        red("No state file.")
        return
    if not confirm(f"Reset PnL anchor for {asset.upper()}?"):
        return
    s["last_swing_low"] = None
    s["sell_approach_sent"] = False
    save_state(asset, s)
    green(f"{asset.upper()} anchor reset.")

def set_slice(asset, usd):
    s = load_state(asset)
    if not s:
        red("No state file.")
        return
    if not confirm(f"Set {asset.upper()} USD slice to ${usd}?"):
        return
    s["usd_slice"] = float(usd)
    save_state(asset, s)
    green(f"{asset.upper()} slice set to ${usd}.")

def pause(asset):
    if not confirm(f"Pause {asset.upper()} trading?"):
        return
    run(f"sudo systemctl disable --now {timer_name(asset)}")
    yellow(f"{asset.upper()} paused.")

def resume(asset):
    if not confirm(f"Resume {asset.upper()} trading?"):
        return
    run(f"sudo systemctl enable --now {timer_name(asset)}")
    green(f"{asset.upper()} resumed.")

def force_tick(asset):
    header()
    yellow(f"Forcing one tick for {asset.upper()}...\n")
    run(f"op run -- python3 {script_path(asset)}")

# ----------------------------
# Dispatcher
# ----------------------------
def handle(args):
    if not args:
        overview()
        return

    cmd = args[0]

    if cmd == "status" and len(args) == 2:
        status(args[1])
    elif cmd == "reset-anchor" and len(args) == 2:
        reset_anchor(args[1])
    elif cmd == "set-slice" and len(args) == 3:
        set_slice(args[1], args[2])
    elif cmd == "pause" and len(args) == 2:
        pause(args[1])
    elif cmd == "resume" and len(args) == 2:
        resume(args[1])
    elif cmd == "force-tick" and len(args) == 2:
        force_tick(args[1])
    else:
        print("""
Usage:
  leo kraken
  leo kraken status <asset>
  leo kraken reset-anchor <asset>
  leo kraken set-slice <asset> <usd>
  leo kraken pause <asset>
  leo kraken resume <asset>
  leo kraken force-tick <asset>
""")
