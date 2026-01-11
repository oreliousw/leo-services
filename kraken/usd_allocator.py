#!/usr/bin/env python3
# ============================================================
# File: usd_allocator.py
# Version: v1.1.0
#
# Purpose:
#   Centralized portfolio risk + USD allocation logic
#
# Design goals:
#   • Single source of truth
#   • Asset-level USD caps
#   • Asset-level SELL behavior
#   • Safe concurrency across multiple bots
# ============================================================

from typing import Dict

# ------------------------------------------------------------
# CONFIG — EDIT HERE ONLY
# ------------------------------------------------------------

ASSET_LIMITS_USD: Dict[str, float] = {
    "BTC": 100.0,
    "ETH": 100.0,
    "XMR": 200.0,
    "SOL": 450.0,
    "XRP": 100.0,
}

# Percentage of holdings to sell per sell event
SELL_FRACTIONS: Dict[str, float] = {
    "BTC": 0.25,
    "ETH": 0.30,
    "XMR": 0.35,
    "SOL": 0.25,
    "XRP": 0.20,
}

MIN_TRADE_USD = 10.0


# ------------------------------------------------------------
# CORE API
# ------------------------------------------------------------

def get_allocatable_usd(
    asset: str,
    usd_total_available: float,
    usd_committed_by_asset: float,
) -> float:

    asset = asset.upper()

    if asset not in ASSET_LIMITS_USD:
        raise ValueError(f"Asset '{asset}' not defined in ASSET_LIMITS_USD")

    asset_cap = ASSET_LIMITS_USD[asset]

    asset_remaining = asset_cap - usd_committed_by_asset
    if asset_remaining <= 0:
        return 0.0

    tradeable_usd = min(asset_remaining, usd_total_available)

    if tradeable_usd < MIN_TRADE_USD:
        return 0.0

    return round(tradeable_usd, 2)


def get_sell_fraction(asset: str) -> float:
    """
    Returns the fraction of holdings allowed to sell per sell event.
    """
    asset = asset.upper()

    if asset not in SELL_FRACTIONS:
        raise ValueError(f"Asset '{asset}' not defined in SELL_FRACTIONS")

    return SELL_FRACTIONS[asset]


# ------------------------------------------------------------
# OPTIONAL DEBUG / VISIBILITY
# ------------------------------------------------------------

def allocation_snapshot(
    usd_total_available: float,
    usd_committed_map: Dict[str, float],
) -> Dict[str, float]:

    snapshot = {}

    for asset, cap in ASSET_LIMITS_USD.items():
        committed = usd_committed_map.get(asset, 0.0)
        snapshot[asset] = get_allocatable_usd(
            asset,
            usd_total_available,
            committed,
        )

    return snapshot
