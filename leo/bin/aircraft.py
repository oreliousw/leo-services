#!/usr/bin/env python3
# ============================================================
# Leo ▸ Aircraft Module
# File: aircraft.py
#
# Responsibilities:
#   • Aircraft pricing scans
#   • Wrapper around aircraft_price_scan.py
#
# Design goals:
#   • Preserve existing Leo behavior
#   • No environment leakage
#   • Operator-friendly usage
# ============================================================

import sys
from pathlib import Path
from core import run, header, yellow, red

# ============================================================
# Paths
# ============================================================
HOME = Path.home()
LEO_BASE = HOME / "leo-services"

AIRCRAFT_DIR = LEO_BASE / "aircraft"
AIRCRAFT_VENV = AIRCRAFT_DIR / "venv" / "bin" / "python"
AIRCRAFT_SCAN = AIRCRAFT_DIR / "aircraft_price_scan.py"

# ============================================================
# Aircraft pricing
# ============================================================
def price_scan(args):
    header()

    if not AIRCRAFT_SCAN.exists():
        red("✖ aircraft_price_scan.py not found")
        print(f"  {AIRCRAFT_SCAN}")
        return

    if not AIRCRAFT_VENV.exists():
        red("✖ Aircraft venv not found")
        print(f"  {AIRCRAFT_VENV}")
        print("\nCreate it with:")
        print("  cd ~/leo-services/aircraft")
        print("  python3 -m venv venv")
        print("  source venv/bin/activate")
        print("  pip install requests beautifulsoup4 selenium webdriver-manager")
        return

    if not args:
        yellow("Aircraft pricing usage:\n")
        print('  leo aircraft price "Cessna 172K"')
        print('  leo aircraft price "Piper Cherokee 140" --save\n')
        return

    query = " ".join(args)
    yellow("Running aircraft price scan...\n")
    run(f'"{AIRCRAFT_VENV}" "{AIRCRAFT_SCAN}" {query}')

# ============================================================
# Dispatcher
# ============================================================
def handle(args):
    """
    Entry point from leo:
      leo aircraft
      leo aircraft price "<model>"
    """
    if not args:
        header()
        print("LEO ▸ AIRCRAFT\n")
        print("Usage:")
        print('  leo aircraft price "Cessna 172K"')
        print('  leo aircraft price "Piper Cherokee 140" --save\n')
        return

    cmd = args[0]

    if cmd == "price":
        price_scan(args[1:])
    else:
        print("""
Usage:
  leo aircraft
  leo aircraft price "<aircraft model>"
""")
