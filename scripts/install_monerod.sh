#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# Mr O — install_monerod.sh
# Production Monero Node Installer (2025)
# EXACT VM-style install:
#   ✔ Official binaries
#   ✔ Own monero user
#   ✔ /opt/monero for binaries
#   ✔ /var/lib/monero for data
#   ✔ systemd service
#   ✔ Safe defaults (restricted RPC, internal binding)
# ─────────────────────────────────────────────────────────────

MONERO_DATA_DIR="/var/lib/monero"
PUBLIC_RPC="no"     # set to "yes" only if you want remote RPC open
PRUNE_NODE="no"      # set "yes" to enable pruning

P2P_PORT=18080
RPC_PORT=18081
ZMQ_PORT=18083

echo "=== Installing Monero Node (VM-Verified Method) ==="
sleep 1

# ─────────────────────────────────────────────────────────────
# 1) Install dependencies
# ─────────────────────────────────────────────────────────────
sudo apt update
sudo apt install -y curl wget tar bzip2

echo "✔ Dependencies installed"

# ─────────────────────────────────────────────────────────────
# 2) Create monero system user (exact VM method)
# ─────────────────────────────────────────────────────────────
if ! id monero >/dev/null 2>&1; then
  sudo useradd -r -m -U -s /usr/sbin/nologin monero
fi

echo "✔ monero system user ready"

# ─────────────────────────────────────────────────────────────
# 3) Download latest Monero CLI release
# ─────────────────────────────────────────────────────────────
sudo rm -rf /opt/monero
sudo mkdir -p /opt/monero
sudo chown -R $USER:$USER /opt/monero

cd /opt/monero

LATEST_URL="$(curl -s https://api.github.com/repos/monero-project/monero/releases/latest \
  | grep browser_download_url \
  | grep linux-x64 \
  | cut -d '"' -f 4 | head -n 1)"

if [[ -z "$LATEST_URL" ]]; then
  echo "❌ ERROR: Could not fetch latest Monero release."
  exit 1
fi

echo "Downloading Monero from:"
echo "$LATEST_URL"
sleep 1

wget -qO monero.tar.bz2 "$LATEST_URL"
tar -xjf monero.tar.bz2 --strip-components=1
rm -f monero.tar.bz2

# Symlink executables (exactly like VM)
sudo ln -sf /opt/monero/monerod /usr/local/bin/monerod
sudo ln -sf /opt/monero/monero-wallet-cli /usr/local/bin/monero-wallet-cli

echo "✔ Monero binaries installed"

# ─────────────────────────────────────────────────────────────
# 4) Prepare blockchain directories
# ─────────────────────────────────────────────────────────────
sudo mkdir -p "$MONERO_DATA_DIR"
sudo chown -R monero:monero "$MONERO_DATA_DIR"

echo "✔ Data directory ready: $MONERO_DATA_DIR"

# ─────────────────────────────────────────────────────────────
# 5) Configure RPC + prune settings
# ─────────────────────────────────────────────────────────────
RPC_BIND="127.0.0.1"
if [[ "$PUBLIC_RPC" == "yes" ]]; then
  RPC_BIND="0.0.0.0"
fi

PRUNE_ARG=""
if [[ "$PRUNE_NODE" == "yes" ]]; then
  PRUNE_ARG="--prune-blockchain"
fi

# ─────────────────────────────────────────────────────────────
# 6) Create systemd service
# ─────────────────────────────────────────────────────────────
sudo tee /etc/systemd/system/monerod.service >/dev/null <<EOF
[Unit]
Description=Monero Daemon (Mr O)
After=network.target
Wants=network-online.target

[Service]
User=monero
Group=monero
Type=simple
ExecStart=/usr/local/bin/monerod \
  --data-dir $MONERO_DATA_DIR \
  --rpc-bind-ip $RPC_BIND \
  --rpc-bind-port $RPC_PORT \
  --restricted-rpc \
  --confirm-external-bind \
  --p2p-bind-ip 0.0.0.0 \
  --p2p-bind-port $P2P_PORT \
  --zmq-pub tcp://127.0.0.1:$ZMQ_PORT \
  --out-peers 64 \
  --in-peers 32 \
  --log-level 1 \
  $PRUNE_ARG

Restart=always
RestartSec=10
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

echo "✔ systemd service created"

# ─────────────────────────────────────────────────────────────
# 7) Enable & Start monerod
# ─────────────────────────────────────────────────────────────
sudo systemctl daemon-reload
sudo systemctl enable monerod
sudo systemctl restart monerod

echo ""
echo "🎉 Monero Node Installed & Running!"
echo ""
echo "Check sync progress:"
echo "  sudo journalctl -u monerod -f"
echo ""
echo "Binary location:  /opt/monero"
echo "Data directory:   $MONERO_DATA_DIR"
echo ""
