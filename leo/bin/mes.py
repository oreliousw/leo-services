#!/usr/bin/env python3
# ============================================================
# Leo ▸ MES Module
# File: mes.py
#
# Responsibilities:
#   • Deploy MES runtime files + systemd units
#   • Restart services (live / demo control)
#   • Provide Leo-native MES operator interface
#   • Call internal .mes-run backend (hidden)
#
# Design goals:
#   • Zero behavior change from legacy MES
#   • Single operator interface (leo mes)
#   • mes-run fully demoted to internal plumbing
# ============================================================

import sys
from pathlib import Path
from core import run, header, confirm, green, yellow, red

# ============================================================
# Paths
# ============================================================
HOME = Path.home()
LEO_BASE = HOME / "leo-services"

MES_SRC_DIR = LEO_BASE / "mes"
MES_BIN_DEST = Path("/opt/mes")

# Internal backend (demoted)
MES_RUN = LEO_BASE / "leo" / "bin" / ".mes-run"

MES_RUNTIME_FILES = [
    "mes_scalp.py",
    "mes_swing.py",
]

MES_SERVICES = [
    "mes_scalp_demo.service",
    "mes_scalp_live.service",
    "mes_swing_demo.service",
    "mes_swing_live.service",
]

# ============================================================
# Deploy logic (matches legacy behavior)
# ============================================================
def deploy_mes(demo_only=False):
    header()

    scope = (
        "DEMO services only (live files updated, not restarted)"
        if demo_only else
        "ALL services"
    )

    yellow(f"Deploying latest MES code → {scope}\n")

    # ---------------- Copy runtime files ----------------
    for fname in MES_RUNTIME_FILES:
        src = MES_SRC_DIR / fname
        if not src.exists():
            red(f"Missing source file: {src}")
            return

        dest = MES_BIN_DEST / fname
        run(f"sudo cp -f {src} {dest}")
        run(f"sudo chmod 644 {dest}")
        green(f"✔ Copied {fname} → {dest}")

    # ---------------- Reload systemd ----------------
    yellow("\nReloading systemd daemon...")
    ok, out = run("sudo systemctl daemon-reload", capture=True)
    if ok:
        green("✔ daemon-reload complete")
    else:
        red("✖ daemon-reload failed")
        print(out)
        return

    # ---------------- Restart services ----------------
    svcs = (
        [s for s in MES_SERVICES if "demo" in s]
        if demo_only else MES_SERVICES
    )

    yellow(f"\nRestarting {scope.split(' (')[0]}...\n")
    for svc in svcs:
        ok, out = run(f"sudo systemctl restart {svc}", capture=True)
        if ok:
            green(f" ✔ {svc}")
        else:
            red(f" ✖ {svc}")
            if out.strip():
                print(f" → {out.strip()}")

# ============================================================
# Internal MES backend wrapper
# ============================================================
def mes_backend(args):
    if not MES_RUN.exists():
        red("✖ Internal MES backend (.mes-run) not found")
        print(f"  Expected at: {MES_RUN}")
        return

    if not args:
        run(f"{MES_RUN} status")
    else:
        run(f"{MES_RUN} {' '.join(args)}")

# ============================================================
# Dispatcher (Leo-native interface)
# ============================================================
def handle(args):
    """
    Entry point from leo:
      leo mes
      leo mes status
      leo mes scalp demo
      leo mes swing live
      leo mes deploy
      leo mes deploy -d
    """

    # ---------------- Menu ----------------
    if not args:
        header()
        print("LEO ▸ MES\n")
        print(" 1) Status overview")
        print(" 2) Run SCALP demo")
        print(" 3) Run SCALP live")
        print(" 4) Run SWING demo")
        print(" 5) Run SWING live")
        print(" 6) Explain status")
        print(" 7) View journal")
        print(" 8) Deploy MES")
        print(" 9) Return\n")

        sel = input("Select option: ").strip()

        if sel == "1":
            mes_backend(["status"])
        elif sel == "2":
            mes_backend(["scalp", "demo"])
        elif sel == "3":
            if confirm("Run SCALP LIVE?"):
                mes_backend(["scalp", "live"])
        elif sel == "4":
            mes_backend(["swing", "demo"])
        elif sel == "5":
            if confirm("Run SWING LIVE?"):
                mes_backend(["swing", "live"])
        elif sel == "6":
            mode = input("Mode (scalp/swing): ").strip()
            env  = input("Env (demo/live): ").strip()
            mes_backend([mode, "explain", env])
        elif sel == "7":
            mode = input("Mode (scalp/swing): ").strip()
            env  = input("Env (demo/live): ").strip()
            mes_backend([mode, "journal", env, "-f"])
        elif sel == "8":
            if confirm("Deploy MES code and restart services?"):
                deploy_mes(demo_only=False)
        return

    # ---------------- CLI passthrough ----------------
    cmd = args[0]

    if cmd == "deploy":
        demo_only = "-d" in args
        if confirm("Deploy MES code and restart services?"):
            deploy_mes(demo_only=demo_only)
        return

    # Anything else → backend
    mes_backend(args)
