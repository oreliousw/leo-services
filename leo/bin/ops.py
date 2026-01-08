#!/usr/bin/env python3
# ============================================================
# Leo ▸ Ops Module
# File: ops.py
#
# Responsibilities:
#   • Git status / repo health
#   • Operator quick reference
#   • Lightweight ops menu
#
# Safe by design:
#   • Read-only
#   • No system mutations
# ============================================================

from pathlib import Path
from core import run, header, green, yellow, red

# ============================================================
# Paths
# ============================================================
HOME = Path.home()
LEO_BASE = HOME / "leo-services"

# ============================================================
# Git Status
# ============================================================
def git_status():
    header()

    repo = LEO_BASE
    if not (repo / ".git").exists():
        yellow("⚠ No git repo found at ~/leo-services")
        return

    ok, out = run(
        f"git -C {repo} status --short --branch",
        capture=True
    )

    if not ok:
        red("✖ Git status command failed")
        print(out)
        return

    lines = out.strip().splitlines()
    branch = lines[0] if lines else "(unknown branch)"
    changes = lines[1:]

    print(f"📂 Repo: {repo}")
    print(f"🌿 Branch: {branch}")

    if not changes:
        green("✔ Repo clean — all changes committed")
    else:
        yellow("\n⚠ Local changes detected:")
        for line in changes[:12]:
            print(f"  {line}")
        if len(changes) > 12:
            print("  ... (more changes)")
        print("\n→ Ready to commit & push when you are")

# ============================================================
# Quick Reference
# ============================================================
def quick_ref():
    header()
    print("""
=========== LEO QUICK REFERENCE ===========

System status:
  leo status
  systemctl --failed
  journalctl -p 3 -xb

MES:
  leo mes status
  leo mes deploy
  leo mes deploy -d

Kraken:
  leo kraken
  leo kraken status btc
  leo kraken pause xrp
  leo kraken resume xrp

Wallets:
  leo wallets
  leo wallets backup
  leo wallets restore --verify

Git:
  leo ops git

==========================================
""")

# ============================================================
# Ops Menu
# ============================================================
def menu():
    while True:
        header()
        print("LEO ▸ OPS MENU\n")
        print(" 1) Git Status")
        print(" 2) Quick Reference")
        print(" 3) Return\n")

        sel = input("Select option: ").strip()

        if sel == "1":
            git_status()
            input("\nPress ENTER to continue...")
        elif sel == "2":
            quick_ref()
            input("\nPress ENTER to return...")
        elif sel in ("3", "q", "x", "exit"):
            return
        else:
            print("Invalid selection.\n")

# ============================================================
# Dispatcher
# ============================================================
def handle(args):
    """
    Entry point from leo:
      leo ops
      leo ops git
      leo ops ref
    """
    if not args:
        menu()
        return

    cmd = args[0]

    if cmd == "git":
        git_status()
    elif cmd in ("ref", "help"):
        quick_ref()
    else:
        print("""
Usage:
  leo ops
  leo ops git
  leo ops ref
""")
