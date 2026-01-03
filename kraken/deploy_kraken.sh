#!/usr/bin/env bash
set -e

SERVICE_NAME="kraken_trade.service"
TIMER_NAME="kraken_trade.timer"
REPO_DIR="/home/ubu/leo-services/kraken"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== Kraken Deploy ==="

echo "Stopping timer..."
sudo systemctl disable --now $TIMER_NAME || true

echo "Ensuring symlinks..."
sudo rm -f $SYSTEMD_DIR/$SERVICE_NAME
sudo rm -f $SYSTEMD_DIR/$TIMER_NAME

sudo ln -s $REPO_DIR/$SERVICE_NAME $SYSTEMD_DIR/$SERVICE_NAME
sudo ln -s $REPO_DIR/$TIMER_NAME   $SYSTEMD_DIR/$TIMER_NAME

echo "Reloading systemd..."
sudo systemctl daemon-reload

echo "Starting timer..."
sudo systemctl enable --now $TIMER_NAME

echo "Timer status:"
systemctl list-timers | grep kraken || true

echo "Optional test run (service context)..."
sudo systemctl start kraken_trade.service

echo "=== Deploy Complete ==="
