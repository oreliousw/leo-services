#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
# XMRig Installer v2 (FULL CPU OPTIMIZED)
# Builds with MSR, AVX2/AVX512, NUMA, full RandomX backend.
# Installs to /opt/xmrig and creates a full CPU-tuned config.
# ─────────────────────────────────────────────

USER_NAME=${SUDO_USER:-ubu}
INSTALL_DIR="/opt/xmrig"
SERVICE="/etc/systemd/system/xmrig.service"

echo "[1/7] Installing dependencies…"
apt update
apt install -y \
    git build-essential cmake automake libtool autoconf \
    libuv1-dev libssl-dev libhwloc-dev libcpuid-dev

echo "[2/7] Creating installation directory…"
mkdir -p "$INSTALL_DIR"
chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"

echo "[3/7] Cloning XMRig…"
cd /tmp
rm -rf xmrig
git clone https://github.com/xmrig/xmrig.git
cd xmrig
mkdir build && cd build

echo "[4/7] Compiling XMRig with FULL CPU backend…"
cmake .. \
  -DXMRIG_DEPS=ON \
  -DWITH_HWLOC=ON \
  -DWITH_LIBCPUID=ON \
  -DWITH_MSR=ON \
  -DWITH_ASM=ON \
  -DWITH_CUDA=OFF \
  -DWITH_OPENCL=OFF

make -j"$(nproc)"

echo "[5/7] Installing XMRig into /opt/xmrig…"
install -m 755 xmrig "$INSTALL_DIR"/xmrig

echo "[6/7] Setting MSR raw I/O permissions…"
modprobe msr || true
setcap cap_sys_rawio=ep "$INSTALL_DIR"/xmrig || true

echo "[7/7] Creating optimized xmrig.json…"

cat > "$INSTALL_DIR/xmrig.json" <<EOF
{
  "autosave": true,
  "auto-config": false,
  "donate-level": 1,

  "cpu": {
    "enabled": true,
    "priority": 3,

    "huge-pages": true,
    "huge-pages-jit": true,
    "hw-aes": true,
    "asm": true,
    "yield": false,

    "force-cpu": true,
    "force-max-threads": true,
    "max-threads-hint": 100,

    "rx": {
      "1gb-pages": true,
      "rdmsr": true,
      "wrmsr": true,
      "numa": true
    },

    "threads": [
      { "index": 0 }, { "index": 1 }, { "index": 2 }, { "index": 3 },
      { "index": 4 }, { "index": 5 }, { "index": 6 }, { "index": 7 },
      { "index": 8 }, { "index": 9 }, { "index": 10 }, { "index": 11 },
      { "index": 12 }, { "index": 13 }, { "index": 14 }, { "index": 15 }
    ]
  },

  "opencl": { "enabled": false },
  "cuda": { "enabled": false },

  "http": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 18092,
    "access-token": null,
    "restricted": false
  },

  "pools": [
    {
      "url": "127.0.0.1:3333",
      "user": "48GugGo1NLXDV59yV2n7kfdTZJSWqPHBvCBsS6Z48ZnqWLGnD4nbiT9CeRJNQtgeyBew7JfSiTp5fRqhe9E6cPBuLPHwTte",
      "pass": "x",
      "rig-id": "leo-baremetal",
      "keepalive": true,
      "tls": false
    }
  ]
}
EOF

chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"

echo "Creating systemd service…"

cat > "$SERVICE" <<EOF
[Unit]
Description=XMRig Miner (Mr O — Full CPU Build)
After=network.target p2pool.service
Requires=p2pool.service

[Service]
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/xmrig -c $INSTALL_DIR/xmrig.json
Restart=always
Nice=10
CPUWeight=90
CapabilityBoundingSet=CAP_SYS_RAWIO
AmbientCapabilities=CAP_SYS_RAWIO

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable xmrig
systemctl restart xmrig

echo "✓ XMRig FULL CPU installation complete!"
echo "View logs: sudo journalctl -u xmrig -f"
