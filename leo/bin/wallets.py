#!/usr/bin/env python3
# ============================================================
# Leo ▸ Wallets Module
# File: wallets.py
# ============================================================

import tarfile
import secrets
import tempfile
from pathlib import Path
from datetime import date
from getpass import getpass

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

BITCOIN_CONF = "/mnt/monero/bitcoin/bitcoin.conf"
BITCOIN_DIR = HOME / ".bitcoin"
MONERO_WALLET = HOME / "wallets" / "myVault"
SOLANA_KEYPAIR = HOME / "wallets" / "solana" / "id.json"

# ============================================================
# Bitcoin Wallet Menu
# ============================================================
def bitcoin_cli():
    btc = f"bitcoin-cli -conf={BITCOIN_CONF}"

    while True:
        header()
        print("LEO ▸ BITCOIN WALLET\n")
        print(" 1) Show balance")
        print(" 2) Show receive address")
        print(" 3) Send BTC")
        print(" 4) Run raw bitcoin-cli")
        print(" 5) Back\n")

        sel = input("Select option: ").strip()

        if sel == "1":
            header()
            run(f"{btc} getbalance", check=False)
            input("\nPress ENTER to continue...")

        elif sel == "2":
            header()
            run(f"{btc} getnewaddress", check=False)
            input("\nPress ENTER to continue...")

        elif sel == "3":
            header()
            addr = input("Destination address: ").strip()
            amt = input("Amount BTC: ").strip()

            if not addr or not amt:
                red("✖ Address and amount required")
                input("\nPress ENTER to continue...")
                continue

            confirm_send = input(f"Send {amt} BTC to {addr}? (y/n): ").strip().lower()
            if confirm_send != "y":
                yellow("Cancelled.")
                input("\nPress ENTER to continue...")
                continue

            # --- Secure wallet unlock ---
            pw = getpass("Wallet passphrase (hidden): ").strip()
            if not pw:
                red("✖ Passphrase required")
                input("\nPress ENTER to continue...")
                continue

            run(f'{btc} walletpassphrase "{pw}" 300', check=False)

            # --- Send transaction ---
            run(f'{btc} sendtoaddress "{addr}" {amt}', check=False)

            input("\nPress ENTER to continue...")

        elif sel == "4":
            header()
            yellow("Raw bitcoin-cli (type your command after bitcoin-cli)\n")
            cmd = input("bitcoin-cli ").strip()
            if cmd:
                run(f"{btc} {cmd}", check=False)
            input("\nPress ENTER to continue...")

        elif sel == "5":
            return

# ============================================================
# Monero CLI
# ============================================================
def monero_cli():
    header()
    yellow("Launching Monero Wallet CLI (local node)...\n")
    run(f"monero-wallet-cli --wallet-file {MONERO_WALLET}")

# ============================================================
# Solana CLI
# ============================================================
def solana_cli():
    header()
    yellow("Launching Solana CLI wallet...\n")

    if not SOLANA_KEYPAIR.exists():
        red(f"✖ Solana keypair not found: {SOLANA_KEYPAIR}")
        yellow("Create one with:")
        print(f"  solana-keygen new --outfile {SOLANA_KEYPAIR}")
        return

    run(f"solana config set --keypair {SOLANA_KEYPAIR}", check=False)
    run("solana", check=False)

# ============================================================
# Wallet Backup
# ============================================================
def wallets_backup():
    header()
    print("LEO ▸ WALLETS BACKUP\n")

    backup_items = []

    btc_wallets = BITCOIN_DIR / "wallets"
    if btc_wallets.exists():
        backup_items.append(btc_wallets)
        green(f"✔ Bitcoin wallets detected: {btc_wallets}")
    else:
        yellow("⚠ Bitcoin wallets not found")

    if MONERO_WALLET.exists():
        backup_items.append(MONERO_WALLET)
        key_file = MONERO_WALLET.with_suffix(".keys")
        if key_file.exists():
            backup_items.append(key_file)
        green(f"✔ Monero wallet detected: {MONERO_WALLET}")
    else:
        yellow("⚠ Monero wallet not found")

    if SOLANA_KEYPAIR.exists():
        backup_items.append(SOLANA_KEYPAIR)
        green(f"✔ Solana keypair detected: {SOLANA_KEYPAIR}")
    else:
        yellow("⚠ Solana keypair not found")

    if not backup_items:
        red("✖ No wallets found to back up")
        return

    if not confirm("Create encrypted wallet backup archive?"):
        return

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
                f"Solana keypair: {SOLANA_KEYPAIR if SOLANA_KEYPAIR.exists() else 'N/A'}\n"
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
# Restore Verify
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
            red("✖ Decryption failed")
            return

        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                tar.extractall(tmpdir)
        except Exception as e:
            red(f"✖ Extraction failed: {e}")
            return

        green("\n✔ Backup integrity verification completed")
        print("\nNo files were written to the system.\n")

# ============================================================
# Dispatcher
# ============================================================
def handle(args):
    if not args:
        header()
        print("LEO ▸ WALLETS\n")
        print(" 1) Bitcoin Wallet CLI")
        print(" 2) Monero Wallet CLI")
        print(" 3) Solana Wallet CLI")
        print(" 4) Backup wallets (encrypted)")
        print(" 5) Restore verify backup")
        print(" 6) Return\n")

        sel = input("Select option: ").strip()

        if sel == "1":
            bitcoin_cli()
        elif sel == "2":
            monero_cli()
        elif sel == "3":
            solana_cli()
        elif sel == "4":
            wallets_backup()
        elif sel == "5":
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
