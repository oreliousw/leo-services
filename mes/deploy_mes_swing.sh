#!/usr/bin/env bash
set -euo pipefail

SERVICE="mes_swing"
SRC="$HOME/leo-services/mes/mes_swing.py"
DST="/opt/mes/mes_swing.py"

echo "Deploying $SERVICE..."

# Sanity checks
if [[ ! -f "$SRC" ]]; then
  echo "❌ Source file not found: $SRC"
  exit 1
fi

if [[ ! -d "/opt/mes" ]]; then
  echo "❌ /opt/mes does not exist"
  exit 1
fi

# Backup existing runtime file
if [[ -f "$DST" ]]; then
  cp "$DST" "$DST.bak.$(date +%Y%m%d-%H%M%S)"
  echo "✔ Backup created"
fi

# Deploy
cp "$SRC" "$DST"
chmod +x "$DST"

echo "✔ mes_swing.py deployed to /opt/mes"
