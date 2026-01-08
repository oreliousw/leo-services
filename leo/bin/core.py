#!/usr/bin/env python3
# ============================================================
# Leo Core Utilities
# File: core.py
#
# Shared helpers used by all Leo modules
# ============================================================

import subprocess
from datetime import datetime
from shutil import which

# ----------------------------
# Shell execution
# ----------------------------
def run(cmd: str, capture=False, check=True):
    if capture:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True
        )
        return result.returncode == 0, result.stdout + result.stderr
    else:
        subprocess.run(cmd, shell=True, check=check)

# ----------------------------
# UI helpers
# ----------------------------
def red(t):    print(f"\033[91m{t}\033[0m")
def green(t):  print(f"\033[92m{t}\033[0m")
def yellow(t): print(f"\033[93m{t}\033[0m")

def header():
    print("\n=== LEO SYSTEM ===")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

# ----------------------------
# Prompt helpers
# ----------------------------
def confirm(prompt: str) -> bool:
    r = input(f"{prompt} (y/n): ").lower().strip()
    return r in ("y", "yes")

def has_cmd(cmd: str) -> bool:
    return which(cmd) is not None
