#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# Mr O — FINAL FIXED C++ P2Pool Installer (2025)
# Includes ALL VM fixes:
#   ✔ Full dependency chain
#   ✔ git submodules (recursive)
#   ✔ miniupnp fix
#   ✔ RandomX/grpc fixes
#   ✔ CURL dev fix
#   ✔ clean systemd service
#   ✔ does NOT touch monerod
# ─────────────────────────────────────────────────────────────

WALLET_ADDRESS="48GugGo1NLXDV59yV2n7kfdTZJSWqPHBvCBsS6Z48ZnqWLGnD4nbiT9CeRJNQtgeyBew7JfSiTp5fRqhe9E6cPBuLPHwTte"
RPC_PORT=18081
STRATUM_PORT=3333
CORES=$(nproc --all)

echo "=== Installing C++ P2Pool (with all VM fixes) ==="
echo "Wallet: $WALLET_ADDRESS"
echo "CPU cores: $CORES"
sleep 1

# ─────────────────────────────────────────────────────────────
# 0) Install ALL required dependencies (VM verified)
# ─────────────────────────────────────────────────────────────
sudo apt update
sudo apt install -y \
  git build-essential cmake pkg-config \
  libboost-all-dev libzmq3-dev cppzmq-dev \
  libuv1-dev libevent-dev libnorm-dev \
  libssl-dev libcurl4-openssl-dev \
  automake autoconf libtool \
  ninja-build

echo "✔ Dependencies installed (VM complete set)"

# ─────────────────────────────────────────────────────────────
# 1) Prepare directory
# ─────────────────────────────────────────────────────────────
sudo rm -rf /opt/p2pool
sudo mkdir -p /opt/p2pool
sudo chown -R $USER:$USER /opt/p2pool

cd /opt/p2pool

# ─────────────────────────────────────────────────────────────
# 2) Clone repo + initialize ALL submodules (most important fix)
# ─────────────────────────────────────────────────────────────
git clone --recursive https://github.com/SChernykh/p2pool.git .

# In case recursive failed earlier:
git submodule update --init --recursive

echo "✔ Repo + submodules initialized"

# ─────────────────────────────────────────────────────────────
# 3) Build (CMake + Make) — this matches VM build
# ─────────────────────────────────────────────────────────────
mkdir -p build
cd build

echo "🛠 Running CMake with correct flags..."
cmake .. -DCMAKE_BUILD_TYPE=Release

echo "🛠 Compiling P2Pool (this is what produced all the green text in VM)..."
make -j"$CORES"

echo "✔ C++ P2Pool compiled successfully"

# ─────────────────────────────────────────────────────────────
# 4) Systemd service (same config used in VM)
# ─────────────────────────────────────────────────────────────
sudo tee /etc/systemd/system/p2pool.service >/dev/null <<EOF
[Unit]
Description=P2Pool C++ (Mr O Node)
After=network.target monerod.service
Requires=monerod.service

[Service]
User=$USER
Group=$USER
WorkingDirectory=/opt/p2pool/build
ExecStart=/opt/p2pool/build/p2pool \
  --host 127.0.0.1 \
  --rpc-port $RPC_PORT \
  --stratum 127.0.0.1:$STRATUM_PORT \
  --wallet "$WALLET_ADDRESS" \
  --in-peers 16 --out-peers 32 \
  --log-level 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "✔ systemd service created"

# ─────────────────────────────────────────────────────────────
# 5) Start + enable P2Pool
# ─────────────────────────────────────────────────────────────
sudo systemctl daemon-reload
sudo systemctl enable p2pool
sudo systemctl restart p2pool

echo ""
echo "🎉 P2Pool C++ (VM-fixed version) is installed and running!"
echo "Check logs:"
echo "  sudo journalctl -u p2pool -f"
echo ""
echo "Binary located at:"
echo "  /opt/p2pool/build/p2pool"
echo ""
