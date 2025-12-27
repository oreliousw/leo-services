#!/usr/bin/env bash
set -euo pipefail

# ───────────────────────────────────────────────
#  P2POOL C++ — FULL 2025 BUILD (Mr O)
#  Includes:
#    ✔ Full JSON Stats Server
#    ✔ RandomX
#    ✔ miniupnp
#    ✔ grpc / protobuf
#    ✔ API server on port 37889
# ───────────────────────────────────────────────

WALLET="48GugGo1NLXDV59yV2n7kfdTZJSWqPHBvCBsS6Z48ZnqWLGnD4nbiT9CeRJNQtgeyBew7JfSiTp5fRqhe9E6cPBuLPHwTte"
CORES=$(nproc)
INSTALL_DIR="/opt/p2pool"

echo "👉 Building P2Pool C++ with full stats/HTTP API"
echo "👉 Wallet: $WALLET"
echo "👉 Cores detected: $CORES"
sleep 1

# ───────────────────────────────────────────────
# Install Dependencies
# ───────────────────────────────────────────────
sudo apt update
sudo apt install -y \
  git cmake build-essential pkg-config \
  libssl-dev libzmq3-dev cppzmq-dev \
  libuv1-dev libevent-dev libnorm-dev \
  libmicrohttpd-dev libcurl4-openssl-dev \
  nlohmann-json3-dev protobuf-compiler \
  libprotobuf-dev libminiupnpc-dev

# ───────────────────────────────────────────────
# Prepare directory
# ───────────────────────────────────────────────
sudo rm -rf $INSTALL_DIR
sudo mkdir -p $INSTALL_DIR
sudo chown -R $USER:$USER $INSTALL_DIR

cd /opt
git clone --recursive https://github.com/SChernykh/p2pool.git
cd p2pool

git submodule update --init --recursive

# ───────────────────────────────────────────────
# Build P2Pool (with stats enabled)
# ───────────────────────────────────────────────
mkdir -p build
cd build

cmake \
  -DWITH_RANDOMX=ON \
  -DWITH_MINIUPNP=ON \
  -DENABLE_HTTP_API=ON \
  -DSTATIC=OFF \
  ..

make -j"$CORES"

echo "✔ DONE — p2pool compiled."

# ───────────────────────────────────────────────
# Create systemd service
# ───────────────────────────────────────────────
sudo tee /etc/systemd/system/p2pool.service >/dev/null <<EOF
[Unit]
Description=P2Pool C++ (Mr O 2025 Full API)
After=network.target monerod.service
Requires=monerod.service

[Service]
User=${USER}
Group=${USER}
WorkingDirectory=$INSTALL_DIR

ExecStart=$INSTALL_DIR/build/p2pool \
  --host 127.0.0.1 \
  --rpc-port 18081 \
  --stratum 127.0.0.1:3333 \
  --api-port 37889 \
  --wallet $WALLET \
  --in-peers 16 \
  --out-peers 32 \
  --log-level 2

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# ───────────────────────────────────────────────
# Enable service
# ───────────────────────────────────────────────
sudo systemctl daemon-reload
sudo systemctl enable p2pool
sudo systemctl restart p2pool

echo ""
echo "🎉 P2Pool FULL API BUILD INSTALLED!"
echo "👉 Stats JSON available at:"
echo "   http://127.0.0.1:37889/stats"
echo ""
echo "👉 Check logs:"
echo "   sudo journalctl -u p2pool -f"
