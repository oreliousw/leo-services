#!/usr/bin/env bash
set -e

REPO_DIR="/home/ubu/leo-services/kraken"
SYSTEMD_DIR="/etc/systemd/system"

UNITS=(
  "kraken_btc.service"
  "kraken_btc.timer"
  "kraken_xmr.service"
  "kraken_xmr.timer"
)

echo "=== Kraken Deploy (symlink mode) ==="

echo "Stopping timers..."
for U in "${UNITS[@]}"; do
  if [[ "$U" == *.timer ]]; then
    sudo systemctl disable --now "$U" || true
  fi
done

echo "Refreshing symlinks..."
for U in "${UNITS[@]}"; do
  sudo rm -f "$SYSTEMD_DIR/$U"
  sudo ln -s "$REPO_DIR/$U" "$SYSTEMD_DIR/$U"
done

echo "Reloading systemd..."
sudo systemctl daemon-reload

echo "Starting timers..."
sudo systemctl enable --now kraken_btc.timer
sudo systemctl enable --now kraken_xmr.timer

echo "Active Kraken timers:"
systemctl list-timers | grep kraken || true

echo "Optional test tick..."
sudo systemctl start kraken_btc.service || true
sudo systemctl start kraken_xmr.service || true

echo "=== Deploy Complete ==="
