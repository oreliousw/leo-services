# Kraken Trader Changelog

## v2.1 — Awareness & Reporting Upgrade (2026-01-03)
- Added 6:00 AM Daily Portfolio Snapshot (Telegram)
- Includes BTC price + 24h % change
- Kraken trading slice valuation (BTC + USD)
- Ledger core BTC reference estimation
- Total portfolio value output
- Unrealized P/L vs previous day (USD + %)
- Engine remains signals-only, no auto-execution

## v2.0 — Swing Rotation Baseline
- Introduced Core + Trading Slice model (30% rotation)
- Buy trigger: -4% pullback from swing high
- Sell trigger: +5% recovery from entry
- State engine: idle → hold → reset
- Telegram alerts only
