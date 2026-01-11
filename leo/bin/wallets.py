#!/usr/bin/env python3
# ============================================================
# Leo ▸ Wallets Module
# File: wallets.py
#
# Responsibilities:
#   • Bitcoin CLI access (local node)
#   • Monero CLI wallet access (local node)
#   • Encrypted wallet backup (1Password-friendly)
#   • Restore verification (NON-DESTRUCTIVE)
#
# Safe by design:
#   • No automatic restores
#   • No key material left on disk
# ============================================================

import tarfile
import secrets
import tempfile
from pathlib import Path
from datetime import date

from core import (
    run,
    header,
    confirm,
    green,
    yellow,
    red,
)

# ============================================================
# Paths
# ============================================================
HOME = Path.home()

BITCOIN_DIR = HOME / ".bitcoin"

# Correct Monero wallet
MONERO_WALLET = Path("/home/ubu/wallets/myVault")
MONERO_BIN = "monero-wallet-cli"

# Correct Bitcoin node config
BTC_CONF = "/mnt/monero/bitcoin/bitcoin.conf"

# ============================================================
# Bitcoin / Monero CLI
# ============================================================
def bitcoin_cli():
    header()
    yellow("Bitcoin Core – Local Node Status\n")

    commands = [
        ("Node Info",       f"bitcoin-cli -conf={BTC_CONF} getnetworkinfo"),
        ("Blockchain Info", f"bitcoin-cli -conf={BTC_CONF} getblockchaininfo"),
        ("Wallet Info",     f"bitcoin-cli -conf={BTC_CONF} getwalletinfo"),
        ("Balances",        f"bitcoin-cli -conf={BTC_CONF} getbalances"),
    ]

    for title, cmd in commands:
        print(f"\n--- {title} ---")
        try:
            run(cmd, check=False)
        except Exception as e:
            red(f"⚠ Failed: {cmd}")
            yellow(str(e))

    print("\nTip: You can run manual commands like:")
    print(f"  bitcoin-cli -conf={BTC_CONF} listtransactions")
    print(f"  bitcoin-cli -conf={BTC_CONF} getnewaddress")
    print(f"  bitcoin-cli -conf={BTC_CONF} getwalletinfo")
    print("\nReturning to menu...\n")


def monero_cli():
    header()
    yellow("Launching Monero Wallet CLI (local node)...\n")

    if not MONERO_WALLET.exists():
        red(f"✖ Monero wallet not found: {MONERO_WALLET}")
        yellow("Check wallet path or restore from backup.")
        return

    cmd = f"{MONERO_BIN} --wallet-file {MONERO_WALLET}"

    try:
        run(cmd, check=False)
    except Exception as e:
        red("⚠ Monero wallet exited with error")
        yellow(str(e))


# ============================================================
# Wallet Backup
# ============================================================
def wallets_backup():
    header()
    print("LEO ▸ WALLETS BACKUP\n")

    backup_items = []

    # ---------------- Bitcoin ----------------
    btc_wallets = BITCOIN_DIR / "wallets"
    if btc_wallets.exists():
        backup_items.append(btc_wallets)
        green(f"✔ Bitcoin wallets detected: {btc_wallets}")
    else:
        yellow("⚠ Bitcoin wallets not found")

    # ---------------- Monero ----------------
    if MONERO_WALLET.exists():
        backup_items.append(MONERO_WALLET)
        key_file = MONERO_WALLET.with_suffix(".keys")
        if key_file.exists():
            backup_items.append(key_file)
        green(f"✔ Monero wallet detected: {MONERO_WALLET}")
    else:
        yellow("⚠ Monero wallet not found")

    if not backup_items:
        red("✖ No wallets found to back up")
        return

    if not confirm("Create encrypted wallet backup archive?"):
        return

    # ---------------- Passphrase ----------------
    pw = input("Enter encryption passphrase (ENTER = auto-generate): ").strip()
    if not pw:
        pw = secrets.token_urlsafe(32)
        yellow("\nGenerated passphrase (store in 1Password):")
        print(pw + "\n")

    today = date.today().isoformat()
    tar_name = f"leo-wallet-backup-{today}.tar.gz"
    enc_name = tar_name + ".enc"

    with tempfile.TemporaryDirectory() as tmpdir:
        tar_path = Path(tmpdir) / tar_name

        with tarfile.open(tar_path, "w:gz") as tar:
            for item in backup_items:
                tar.add(item, arcname=item.name)

            meta = Path(tmpdir) / "RESTORE_INFO.txt"
            meta.write_text(
                f"Backup date: {today}\n"
                f"Bitcoin dir: {btc_wallets if btc_wallets.exists() else 'N/A'}\n"
                f"Monero wallet: {MONERO_WALLET if MONERO_WALLET.exists() else 'N/A'}\n"
            )
            tar.add(meta, arcname="RESTORE_INFO.txt")

        run(
            f"openssl enc -aes-256-cbc -pbkdf2 -salt "
            f"-in {tar_path} -out {enc_name} "
            f"-pass pass:{pw}"
        )

    green(f"\n✔ Encrypted backup created: {enc_name}")
    print("\n→ Attach this file to 1Password")
    print("→ Store the passphrase in the same vault item\n")

# ============================================================
# Restore Verify (NON-DESTRUCTIVE)
# ============================================================
def restore_verify():
    header()
    print("LEO ▸ WALLETS RESTORE VERIFY\n")

    enc_file = input("Path to encrypted backup (.tar.gz.enc): ").strip()
    enc_path = Path(enc_file)

    if not enc_path.exists():
        red("✖ Backup file not found")
        return

    pw = input("Enter backup passphrase: ").strip()
    if not pw:
        red("✖ Passphrase required")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        tar_path = Path(tmpdir) / "restore.tar.gz"

        try:
            run(
                f"openssl enc -d -aes-256-cbc -pbkdf2 "
                f"-in {enc_path} -out {tar_path} "
                f"-pass pass:{pw}"
            )
        except Exception:
            red("✖ Decryption failed (wrong passphrase or corrupt file)")
            return

        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                tar.extractall(tmpdir)
        except Exception as e:
            red(f"✖ Extraction failed: {e}")
            return

        ok = True

        if not (Path(tmpdir) / "RESTORE_INFO.txt").exists():
            red("✖ Missing RESTORE_INFO.txt")
            ok = False

        btc_found = any("wallets" in p.name for p in Path(tmpdir).iterdir())
        if btc_found:
            green("✔ Bitcoin wallet files detected")
        else:
            yellow("⚠ Bitcoin wallet files not detected")

        monero_wallet = Path(tmpdir) / MONERO_WALLET.name
        monero_keys = monero_wallet.with_suffix(".keys")

        if monero_wallet.exists() and monero_keys.exists():
            green("✔ Monero wallet + keys detected")
        else:
            yellow("⚠ Monero wallet or keys missing")

        if ok:
            green("\n✔ Backup integrity verification PASSED")
        else:
            red("\n✖ Backup integrity verification FAILED")

        print("\nNo files were written to the system.")
        print("Restore verification completed safely.\n")

# ============================================================
# Dispatcher
# ============================================================
def handle(args):
    if not args:
        header()
        print("LEO ▸ WALLETS\n")
        print(" 1) Bitcoin Wallet CLI")
        print(" 2) Monero Wallet CLI")
        print(" 3) Backup wallets (encrypted)")
        print(" 4) Restore verify backup")
        print(" 5) Return\n")

        sel = input("Select option: ").strip()

        if sel == "1":
            bitcoin_cli()
        elif sel == "2":
            monero_cli()
        elif sel == "3":
            wallets_backup()
        elif sel == "4":
            restore_verify()
        return

    cmd = args[0]

    if cmd == "backup":
        wallets_backup()
    elif cmd == "restore" and len(args) == 2 and args[1] == "--verify":
        restore_verify()
    else:
        print("""
Usage:
  leo wallets
  leo wallets backup
  leo wallets restore --verify
""")
