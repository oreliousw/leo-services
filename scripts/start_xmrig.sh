#!/usr/bin/env bash
# Project: Leo Services
# File: start_xmrig.sh
# Version: v1.1.0 — 2025-12-28
# Change: Replaced op-run daemon wrapper — safe env loader + exec (no zombies)
#
set -euo pipefail

# Fetch env vars from 1Password into a temp file
ENV_OUT=$(mktemp)

op run --env-file=/opt/xmrig/env_mining.op.env -- printenv > "$ENV_OUT"

# Export each KEY=VALUE safely (no eval, no syntax errors)
while IFS='=' read -r key value; do
    [ -z "$key" ] && continue
    export "$key=$value"
done < "$ENV_OUT"

rm -f "$ENV_OUT"

# Replace shell with xmrig (no child process left behind)
exec /opt/xmrig/xmrig -c /opt/xmrig/xmrig.json
