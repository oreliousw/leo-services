#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
# Monero Full Node Installer (v2 – 2025-11)
# Clean install, correct LMDB directories,
# systemd service, permissions, and health checks.
# ─────────────────────────────────────────────

USER_NAME=${SUDO_USER:-ubu}
DATA_DIR="/mnt/monero/.bitmonero"
SERVICE="/etc/systemd/system/monerod.service"

echo "[1/7] Installing dependencies…"
apt update
apt install -y \
    curl wget tar ca-certificates \
    build-essential pkg-config \
    libboost-all-dev libunbound-dev \
    libzmq3-dev libsodium-dev \
    libunwind-dev liblzma-dev \
    libreadline-dev libldns-dev \
    libexpat1-dev doxygen graphviz

echo "[2/7] Creating LMDB data directory…"
mkdir -p "$DATA_DIR/lmdb"
chmod 755 /mnt/monero
chmod 755 "$DATA_DIR"
chown -R "$USER_NAME:$USER_NAME" /mnt/monero

echo "[3/7] Downloading latest Monero release…"
cd /tmp
wget -q https://downloads.getmonero.org/cli/monero-linux-x64-v0.18.4.3.tar.bz2
tar -xjf monero-linux-x64-v0.18.4.3.tar.bz2
cd monero-x86_64-linux-gnu-0.18.4.3

echo "[4/7] Installing monerod into /usr/local/bin…"
install -m 755 monerod /usr/local/bin/

echo "[5/7] Creating systemd service…"

cat > "$SERVICE" <<EOF
[Unit]
Description=Monero Full Node (Mr O)
After=network.target

[Service]
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=/mnt/monero
ExecStart=/usr/local/bin/monerod \\
  --data-dir $DATA_DIR \\
  --rpc-bind-ip 127.0.0.1 \\
  --rpc-bind-port 18081 \\
  --p2p-bind-ip 0.0.0.0 \\
  --p2p-bind-port 18080 \\
  --zmq-pub tcp://127.0.0.1:18083 \\
  --confirm-external-bind \\
  --out-peers 64 \\
  --in-peers 32 \\
  --log-level 1 \\
  --non-interactive

LimitNOFILE=65535
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "[6/7] Enabling + starting monerod…"
systemctl daemon-reload
systemctl enable monerod
systemctl restart monerod

echo "[7/7] Health check:"
echo "  curl -s http://127.0.0.1:18081/get_info | jq .status"
echo "  sudo journalctl -u monerod -f"

echo "✓ Monerod installation complete!"
