#!/usr/bin/env python3
"""
node.o169.com – Monero Node Dashboard v3
Now includes MES Auto Trader Summary Card (via systemd logs)

Served via Nginx at /1/ → proxy_pass http://127.0.0.1:8088/
"""

import json
import socket
import subprocess
from datetime import datetime, timedelta
from urllib import request
import re

from flask import Flask, render_template_string

app = Flask(__name__)

# ─────────────────────────────────────────────────────────
# Config – endpoints
# ─────────────────────────────────────────────────────────
XMRIG_API = "http://127.0.0.1:18092/2/summary"
MONEROD_RPC = "http://127.0.0.1:18081/json_rpc"
WALLET_RPC = "http://127.0.0.1:18089/json_rpc"
P2POOL_STATUS = "/var/log/p2pool/status.json"

SERVICE_NAMES = [
    ("monerod.service",      "Monero Node"),
    ("p2pool.service",       "P2Pool"),
    ("xmrig.service",        "XMRig Miner"),
    ("mes_auto.service",     "MES Auto Trader"),
    ("daily-report.service", "Daily Report"),
]

# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def safe_run(cmd):
    try:
        res = subprocess.run(cmd, text=True, capture_output=True, check=False)
        return (res.stdout or res.stderr or "").strip()
    except Exception:
        return ""


def get_file_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def get_json_url(url, headers=None, timeout=3):
    try:
        req = request.Request(url, headers=headers or {})
        with request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def rpc_call(url, method, params=None, timeout=3):
    payload = {"jsonrpc": "2.0", "id": "0", "method": method}
    if params:
        payload["params"] = params
    try:
        data = json.dumps(payload).encode()
        req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()).get("result")
    except Exception:
        return None


def get_recent_errors(hours=2):
    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    return safe_run(["journalctl", "-p", "err", "--since", since, "--no-pager", "-o", "short-iso"])


def get_memory_usage():
    return safe_run(["free", "-h"])


def get_uptime():
    raw = safe_run(["cat", "/proc/uptime"]).split()
    try:
        total_seconds = int(float(raw[0]))
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        return f"{h}h {m}m"
    except Exception:
        return "unknown"


def get_loadavg():
    try:
        with open("/proc/loadavg") as f:
            return " ".join(f.read().split()[0:3])
    except Exception:
        return "unknown"


def get_disk_usage():
    mounts = safe_run(["mount"])
    root = safe_run(["df", "-h", "/"])
    monero = safe_run(["df", "-h", "/mnt/monero"]) if "/mnt/monero" in mounts else ""
    return root, monero


def get_failed_units_count():
    failed = safe_run(["systemctl", "--failed", "--no-legend"])
    return len([l for l in failed.splitlines() if l.strip()])


def get_service_status():
    rows = []
    for unit, label in SERVICE_NAMES:
        raw = safe_run(["systemctl", "is-active", unit]).strip()
        if raw == "active":
            icon = "🟢"
        elif raw in ("activating", "deactivating"):
            icon = "🟡"
        else:
            icon = "🔴"
        rows.append({"unit": unit, "label": label, "state": raw or "unknown", "icon": icon})
    return rows


def get_xmr_price_usd():
    try:
        req = request.Request(
            "https://api.coingecko.com/api/v3/simple/price?ids=monero&vs_currencies=usd",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
            return float(data.get("monero", {}).get("usd", 0))
    except Exception:
        return 0.0

# ─────────────────────────────────────────────────────────
# MES Summary Parsing
# ─────────────────────────────────────────────────────────

def get_mes_summary():
    """Parse last MES Auto Trader run from systemd logs."""
    logs = safe_run([
        "journalctl", "-u", "mes_auto.service",
        "-n", "40", "--no-pager", "-o", "cat"
    ])

    if not logs:
        return {"ok": False, "status": "No logs", "last_run": None}

    lines = logs.splitlines()

    # Determine last run timestamp
    ts_match = re.search(r"(\w{3} \d{1,2} \d{2}:\d{2}:\d{2})", logs)
    last_run = ts_match.group(1) if ts_match else "unknown"

    # Determine last exit status
    if "Deactivated successfully" in logs:
        status = "success"
        icon = "🟢"
    elif "Failed" in logs or "error" in logs.lower():
        status = "failed"
        icon = "🔴"
    else:
        status = "inactive"
        icon = "🟡"

    # Extract skip/execution messages
    skip_msgs = []
    for l in lines:
        if "[MES]" in l:
            skip_msgs.append(l.strip())

    return {
        "ok": True,
        "status": status,
        "icon": icon,
        "last_run": last_run,
        "details": skip_msgs[-5:] if skip_msgs else ["No MES activity found"],
    }


# ─────────────────────────────────────────────────────────
# Main data collection
# ─────────────────────────────────────────────────────────

def gather_data():
    host = socket.gethostname()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    md = rpc_call(MONEROD_RPC, "get_info")
    p2 = get_file_json(P2POOL_STATUS)
    xr = get_json_url(XMRIG_API, headers={"Authorization": "Bearer mro-token"})
    wb = rpc_call(WALLET_RPC, "get_balance")
    txs = rpc_call(WALLET_RPC, "get_transfers", {"in": True})

    uptime = get_uptime()
    loadavg = get_loadavg()
    mem = get_memory_usage()
    root_disk, monero_disk = get_disk_usage()
    failed_units = get_failed_units_count()
    errors = get_recent_errors(2)
    services = get_service_status()

    # IP
    raw_ip = safe_run(["hostname", "-I"]).strip().split()
    ipv4 = next((i for i in raw_ip if "." in i), "unknown")
    ipv6_count = len([i for i in raw_ip if ":" in i])
    ip_info = f"{ipv4} ({ipv6_count} IPv6)"

    # Wallet
    wallet = {"ok": False}
    if wb:
        bal = wb.get("balance", 0) / 1e12
        unlocked = wb.get("unlocked_balance", 0) / 1e12
        price = get_xmr_price_usd()
        usd_val = bal * price if price else None

        last_in = None
        if txs and txs.get("in"):
            t = txs["in"][-1]
            last_in = {
                "amount": t.get("amount", 0) / 1e12,
                "height": t.get("height", "?"),
            }

        wallet.update({
            "ok": True,
            "balance": bal,
            "unlocked": unlocked,
            "price": price,
            "usd": usd_val,
            "last_in": last_in,
        })

    # MES summary
    mes = get_mes_summary()

    return {
        "host": host,
        "now": now,
        "uptime": uptime,
        "loadavg": loadavg,
        "memory": mem,
        "root_disk": root_disk,
        "monero_disk": monero_disk,
        "failed_units": failed_units,
        "ip_info": ip_info,
        "monerod": md,
        "p2pool": p2,
        "xmrig": xr,
        "wallet": wallet,
        "errors": errors,
        "services": services,
        "mes": mes,
    }


# ─────────────────────────────────────────────────────────
# HTML Template (includes MES Summary card)
# ─────────────────────────────────────────────────────────

TEMPLATE = r"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>node.o169.com – Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="10">

  <style>
    body { background:#05060a; color:#eee; font-family:system-ui; margin:0; }
    .wrapper { max-width:960px; margin:0 auto; padding:16px; }
    h1 { margin:0 0 6px; font-size:1.4rem; }
    .subtitle { font-size:.85rem; color:#a0a0b8; margin-bottom:12px; }
    .grid { display:grid; gap:12px; grid-template-columns:1fr; }
    @media (min-width:900px){
      .grid { grid-template-columns:1fr 1fr; }
    }
    .card {
      background:#101320; border-radius:10px;
      padding:12px 14px; box-shadow:0 0 0 1px #1b1f34;
    }
    h2 { margin:0 0 4px; font-size:1rem; }
    pre { font-family:ui-monospace; font-size:.78rem; white-space:pre-wrap; }
    .pill-row { display:flex; flex-wrap:wrap; gap:6px; }
    .pill { padding:3px 8px; border-radius:999px; background:#181b30; border:1px solid #262b4a; font-size:.78rem; }
  </style>
</head>
<body>
<div class="wrapper">

  <h1>node.o169.com – Monero Node</h1>
  <div class="subtitle">Last refresh: {{ now }} • Host: {{ host }}</div>

  <div class="grid">

    <!-- MES SUMMARY CARD -->
    <div class="card">
      <h2>🤖 MES Auto Trader Summary</h2>
      <pre>
Last Run:    {{ mes.last_run }}
Status:      {{ mes.icon }} {{ mes.status }}
Recent:
{% for line in mes.details %}
- {{ line }}
{% endfor %}
      </pre>
    </div>

    <!-- System Summary -->
    <div class="card">
      <h2>🖥 System Summary</h2>
      <pre>
Uptime:     {{ uptime }}
Load Avg:   {{ loadavg }}
IP:         {{ ip_info }}
Failed:     {{ failed_units }}

Disk (/):
{{ root_disk }}
{% if monero_disk %}
Disk (/mnt/monero):
{{ monero_disk }}
{% endif %}
      </pre>
    </div>

    <!-- Services -->
    <div class="card">
      <h2>🔧 Services</h2>
      <div class="pill-row">
      {% for s in services %}
        <div class="pill">{{ s.icon }} {{ s.label }} ({{ s.state }})</div>
      {% endfor %}
      </div>
    </div>

    <!-- Monero Node -->
    <div class="card">
      <h2>⚙️ Monero Node</h2>
      {% if monerod %}
      <pre>
Height:      {{ monerod.height }}
Target:      {{ monerod.target_height }}
Synced:      {{ monerod.height == monerod.target_height }}
Difficulty:  {{ monerod.difficulty }}
Peers:       in {{ monerod.incoming_connections_count }} / out {{ monerod.outgoing_connections_count }}
Tx Pool:     {{ monerod.tx_pool_size }}
Version:     {{ monerod.version }}
      </pre>
      {% else %}
      <pre>RPC unavailable</pre>
      {% endif %}
    </div>

    <!-- Wallet -->
    <div class="card">
      <h2>👛 Wallet</h2>
      {% if wallet.ok %}
      <pre>
Balance:     {{ "%.12f"|format(wallet.balance) }} XMR
Unlocked:    {{ "%.12f"|format(wallet.unlocked) }} XMR
Price:       {% if wallet.price %}${{ "%.2f"|format(wallet.price) }}{% else %}N/A{% endif %}
Value:       {% if wallet.usd %}${{ "%.2f"|format(wallet.usd) }}{% else %}N/A{% endif %}
{% if wallet.last_in %}
Last In:     {{ "%.12f"|format(wallet.last_in.amount) }} XMR (h={{ wallet.last_in.height }})
{% endif %}
      </pre>
      {% else %}
      <pre>Wallet RPC unavailable</pre>
      {% endif %}
    </div>

    <!-- P2Pool -->
    <div class="card">
      <h2>🏊 P2Pool</h2>
      {% if p2pool %}
      <pre>
Height:       {{ p2pool.local_height }}
Miners:       {{ p2pool.miners }}
Hashrate:     {{ p2pool.pool_hashrate }}
Round Hashes: {{ p2pool.round_hashes }}
Last Block:   {{ p2pool.last_block_time }}
      </pre>
      {% else %}
      <pre>P2Pool status unavailable</pre>
      {% endif %}
    </div>

    <!-- XMRig -->
    <div class="card">
      <h2>⛏ XMRig</h2>
      {% if xmrig %}
      <pre>
Hashrate 10s: {{ xmrig.hashrate.total[0] }}
Hashrate 1m:  {{ xmrig.hashrate.total[1] }}
Hashrate 15m: {{ xmrig.hashrate.total[2] }}
Accepted:     {{ xmrig.results.accepted }}
Rejected:     {{ xmrig.results.rejected }}
      </pre>
      {% else %}
      <pre>XMRig API unavailable</pre>
      {% endif %}
    </div>

    <!-- Errors -->
    <div class="card" style="grid-column:1 / -1;">
      <h2>🐛 Errors (last 2h)</h2>
      <pre>{{ errors if errors.strip() else "-- No entries --" }}</pre>
    </div>

  </div>
</div>
</body>
</html>
"""

# Routes
@app.route("/")
@app.route("/1/")
def index():
    return render_template_string(TEMPLATE, **gather_data())

@app.route("/1/errors")
def view_errors():
    err = get_recent_errors(4)
    return "<pre>" + (err or "-- No entries --") + "</pre>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8088)

# ─────────────────────────────────────────────────────────
# MES RAW DIAGNOSTICS PAGE (/4)
# ─────────────────────────────────────────────────────────

import json
from pathlib import Path
from flask import render_template_string

MES_DIAG_PATH = Path("/mnt/mes/latest_diag.json")


def _format_mes_block(diag: dict) -> str:
    """Format one MES decision into dev-style text block."""
    pair = diag.get("pair", "UNKNOWN")
    tstamp = diag.get("time", "N/A")
    decision = diag.get("decision", "SKIPPED")
    direction = diag.get("direction", "NONE")

    atr_current = diag.get("atr_current")
    atr_delta = diag.get("atr_delta")
    atr_trend = diag.get("atr_trend", "unknown")

    macd_fast = diag.get("macd_fast")
    macd_slow = diag.get("macd_slow")
    macd_sep = diag.get("macd_sep")

    rsi_15m = diag.get("rsi_15m")
    rsi_1h = diag.get("rsi_1h")
    rsi_4h = diag.get("rsi_4h")

    structure = diag.get("candle_structure", "unknown")

    tf15 = diag.get("tf_15m", "?")
    tf1h = diag.get("tf_1h", "?")
    tf4h = diag.get("tf_4h", "?")

    rr = diag.get("rr")
    rr_min = diag.get("rr_min")

    reasons = diag.get("reasons", [])

    def fmt(v, fmt_str="{:.5f}"):
        try:
            if v is None:
                return "N/A"
            return fmt_str.format(float(v))
        except Exception:
            return str(v)

    lines = []
    lines.append("[MES AUTO v3.2] Diagnostic Mode ON")
    lines.append(f"Time: {tstamp}")
    lines.append(f"Pair: {pair}")
    lines.append("")
    lines.append(f"• ATR(14) Current .......... {fmt(atr_current)}")
    lines.append(f"• ATR Trend ................ {atr_trend} ({fmt(atr_delta)})")
    lines.append("")
    lines.append(f"• MACD Fast ................ {fmt(macd_fast)}")
    lines.append(f"• MACD Slow ................ {fmt(macd_slow)}")
    lines.append(f"• MACD Slope Separation .... {fmt(macd_sep)}")
    lines.append("")
    lines.append(f"• RSI(15M) ................. {fmt(rsi_15m, '{:.2f}')}")
    lines.append(f"• RSI(1H) .................. {fmt(rsi_1h, '{:.2f}')}")
    lines.append(f"• RSI(4H) .................. {fmt(rsi_4h, '{:.2f}')}")
    lines.append("")
    lines.append(f"• Candle Structure ......... {structure}")
    lines.append("")
    lines.append("TF Alignment:")
    lines.append(f"   15M → {str(tf15).capitalize()}")
    lines.append(f"   1H  → {str(tf1h).capitalize()}")
    lines.append(f"   4H  → {str(tf4h).capitalize()}")
    lines.append("")
    lines.append(
        f"• Projected RR ............. {fmt(rr, '{:.2f}')}R "
        f"(min {fmt(rr_min, '{:.2f}')}R)"
    )
    lines.append("")
    if reasons:
        lines.append("Reasons:")
        for r in reasons:
            lines.append(f" - {r}")
        lines.append("")
    final_icon = "✅" if decision in ("BUY", "SELL") else "⚠"
    lines.append(f"FINAL: {final_icon} {decision} ({direction})")
    lines.append("----------------------------------------------------")
    return "\n".join(lines)


MES_DIAG_TEMPLATE = r"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>MES Auto – Raw Diagnostics</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { background:#05060a; color:#eee; font-family:system-ui; margin:0; padding:16px; }
    h1 { margin:0 0 6px; font-size:1.3rem; }
    h2 { margin:14px 0 4px; font-size:1rem; }
    .meta { font-size:.85rem; color:#a0a0b8; margin-bottom:10px; }
    pre {
      background:#000; border-radius:10px;
      padding:10px 12px;
      font-family:ui-monospace, Menlo, Monaco, Consolas, "SF Mono", monospace;
      font-size:.8rem; line-height:1.35;
      white-space:pre; overflow-x:auto;
    }
    .pair-block { margin-bottom:12px; }
    a { color:#7dc7ff; text-decoration:none; }
    a:hover { text-decoration:underline; }
  </style>
</head>
<body>
  <h1>MES AUTO – Raw Diagnostics</h1>
  <div class="meta">
    {% if latest %}
      Last update: {{ latest.generated_at }} UTC
    {% else %}
      No diagnostics file found (expected {{ path }}).
    {% endif %}
    &nbsp;|&nbsp; <a href="/1/">Back to main dashboard</a>
  </div>

  {% if blocks %}
    {% for pair, block in blocks %}
      <div class="pair-block">
        <h2>{{ pair }}</h2>
        <pre>{{ block }}</pre>
      </div>
    {% endfor %}
  {% else %}
    <p><em>No MES entries to display.</em></p>
  {% endif %}
</body>
</html>
"""


@app.route("/4")
def mes_diag():
    """Raw MES diagnostics page – one block per pair."""
    latest = None
    blocks = []

    if MES_DIAG_PATH.exists():
        try:
            raw = MES_DIAG_PATH.read_text()
            latest = json.loads(raw)
            pairs = latest.get("pairs", {})
            blocks = [
                (pair, _format_mes_block(diag))
                for pair, diag in sorted(pairs.items())
            ]
        except Exception as e:
            blocks = [("ERROR", f"Failed to load diagnostics JSON: {e}")]

    return render_template_string(
        MES_DIAG_TEMPLATE,
        latest=latest,
        blocks=blocks,
        path=str(MES_DIAG_PATH),
    )
