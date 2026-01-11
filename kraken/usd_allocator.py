#!/usr/bin/env python3
# ============================================================
# File: usd_allocator.py
# Version: v1.0.0
#
# Purpose:
#   Centralized USD allocation logic for Kraken spot trading
#
# Design goals:
#   • Single source of truth
#   • Asset-level USD caps
#   • Safe concurrency across multiple bots
#   • Zero exchange-side assumptions
# ============================================================

from typing import Dict

# ------------------------------------------------------------
# CONFIG — EDIT HERE ONLY
# ------------------------------------------------------------

ASSET_LIMITS_USD: Dict[str, float] = {
    "BTC": 100.0,
    "ETH": 100.0,
    "XMR": 100.0,
    "SOL": 100.0,
    "XRP": 100.0,
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
    """
    Returns how much USD this asset is allowed to use RIGHT NOW.

    Args:
        asset (str): Asset symbol (BTC, ETH, XMR)
        usd_total_available (float): Free USD balance on account
        usd_committed_by_asset (float): USD already used by this asset
                                        (open positions, pending orders)

    Returns:
        float: Tradeable USD (0 if not allowed)
    """

    asset = asset.upper()

    if asset not in ASSET_LIMITS_USD:
        raise ValueError(f"Asset '{asset}' not defined in ASSET_LIMITS_USD")

    asset_cap = ASSET_LIMITS_USD[asset]

    # Asset-level remaining allocation
    asset_remaining = asset_cap - usd_committed_by_asset

    if asset_remaining <= 0:
        return 0.0

    # Final safe USD is constrained by BOTH:
    #   • Asset cap
    #   • Actual USD available on account
    tradeable_usd = min(asset_remaining, usd_total_available)

    if tradeable_usd < MIN_TRADE_USD:
        return 0.0

    return round(tradeable_usd, 2)


# ------------------------------------------------------------
# OPTIONAL DEBUG / VISIBILITY
# ------------------------------------------------------------

def allocation_snapshot(
    usd_total_available: float,
    usd_committed_map: Dict[str, float],
) -> Dict[str, float]:
    """
    Returns a per-asset snapshot of remaining allocatable USD.
    Useful for logging / Telegram / diagnostics.
    """

    snapshot = {}

    for asset, cap in ASSET_LIMITS_USD.items():
        committed = usd_committed_map.get(asset, 0.0)
        snapshot[asset] = get_allocatable_usd(
            asset,
            usd_total_available,
            committed,
        )

    return snapshot
