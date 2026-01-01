#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# Project: Leo Services
# File: aircraft_price_scan.py
# Version: v1.0.0 — 2025-12-31
# Change: Hard-coded family scrapers (PA-28 + C172) — deterministic pricing mode
# Note: Bump Version + Change when modifying runtime behavior

Aircraft Price Scan — Family-Locked Market Comparison Tool
"""

import re
import sys
import csv
import pathlib
import statistics
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup


BASE_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ARCHIVE_DIR = DATA_DIR / "archives"
DATA_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Dataclass
# ============================================================
@dataclass
class AircraftListing:
    site: str
    family: str
    title: str
    year: Optional[int]
    price_usd: Optional[int]
    total_time: Optional[int]
    engine_smoh: Optional[int]
    url: str
    avionics_hits: List[str]
    bucket: str
    raw_text: str


# ============================================================
# Regex helpers
# ============================================================
PRICE_RE = re.compile(r"\$(?:\s*|\b)([\d,]+)")
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")
HOURS_RE = re.compile(r"\b(\d{3,6})\s*(?:TT|TTAF|TOTAL TIME|HRS)", re.I)
SMOH_RE = re.compile(r"\b(\d{2,5})\s*(?:SMOH|S\.M\.O\.H\.)", re.I)


def parse_price(text): 
    m = PRICE_RE.search(text); 
    return int(m.group(1).replace(",", "")) if m else None

def parse_year(text):
    m = YEAR_RE.search(text)
    return int(m.group(1)) if m else None

def parse_hours(text):
    m = HOURS_RE.search(text)
    return int(m.group(1)) if m else None

def parse_smoh(text):
    m = SMOH_RE.search(text)
    return int(m.group(1)) if m else None


# ============================================================
# Avionics classifier
# ============================================================
AVIONICS = {
    "g5": ["g5", "garmin g5"],
    "g3x": ["g3x"],
    "430w": ["430w", "gns 430w"],
    "530w": ["530w", "gns 530w"],
    "gtn": ["gtn650", "gtn750"],
    "ifd": ["ifd440", "ifd540", "avidyne ifd"],
    "adsb": ["ads-b", "adsb", "skybeacon", "uavionix"],
    "ap": ["autopilot", "stech", "truetrak", "gfc500", "kfc"],
}

PROJECT_FILTER = [
    "project", "no engine", "no prop", "salvage",
    "parts only", "damaged", "needs rebuild",
]


def detect_avionics(text):
    t = text.lower()
    return [k for k, vals in AVIONICS.items() if any(v in t for v in vals)]


def derive_bucket(hits):
    h = set(hits)
    if "g5" in h or "g3x" in h:
        return "glass_ap" if "ap" in h else "ifr_modern"
    if any(x in h for x in ("430w", "530w", "gtn", "ifd")):
        return "ifr_legacy_gps"
    return "vfr_basic"


def is_project(text):
    t = text.lower()
    return any(p in t for p in PROJECT_FILTER)


# ============================================================
# HTTP
# ============================================================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Leo-Aircraft-Scanner)",
    "Accept-Language": "en-US",
}


def fetch_html(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"[WARN] {url} -> HTTP {r.status_code}")
            return None
        return r.text
    except Exception as e:
        print(f"[WARN] Fetch failed {url}: {e}")
        return None


# ============================================================
# Listing builder
# ============================================================
def make_listing(site, family, text, url):
    if is_project(text):
        return None
    price = parse_price(text)
    if not price:
        return None

    hits = detect_avionics(text)

    return AircraftListing(
        site=site,
        family=family,
        title=text[:150],
        year=parse_year(text),
        price_usd=price,
        total_time=parse_hours(text),
        engine_smoh=parse_smoh(text),
        url=url,
        avionics_hits=hits,
        bucket=derive_bucket(hits),
        raw_text=text,
    )


# ============================================================
# HARD-CODED FAMILY SCRAPERS
# ============================================================

# --------- PA-28 / Cherokee / Arrow ----------
def pa28_sources():
    return [
        ("Trade-A-Plane",
         "https://www.trade-a-plane.com/search?make=PIPER&model=CHEROKEE+140%2F160&s-type=aircraft",
         "div.card, div.search-card, li, article"),

        ("Barnstormers",
         "https://www.barnstormers.com/category-21208-Piper--PA-28-Cherokee.html",
         "div.adBox, div.classified, td"),
    ]


# --------- Cessna 172 ----------
def c172_sources():
    return [
        ("Trade-A-Plane",
         "https://www.trade-a-plane.com/search?make=CESSNA&model=172&s-type=aircraft",
         "div.card, div.search-card, li, article"),

        ("Barnstormers",
         "https://www.barnstormers.com/category-10009-Cessna-172.html",
         "div.adBox, div.classified, td"),
    ]


def scrape_family(family, sources):
    listings = []
    for site, url, sel in sources():
        html = fetch_html(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select(sel):
            text = " ".join(card.get_text(" ", strip=True).split())
            l = make_listing(site, family, text, url)
            if l:
                listings.append(l)
    return listings


# ============================================================
# Stats & CSV
# ============================================================
def median_safe(xs): return int(statistics.median(xs)) if xs else None
def mean_safe(xs): return int(statistics.mean(xs)) if xs else None


def export_csv(rows, path):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "site","family","title","year","price_usd",
            "total_time","engine_smoh","bucket","url","avionics_hits"
        ])
        for r in rows:
            w.writerow([
                r.site, r.family, r.title, r.year or "",
                r.price_usd or "", r.total_time or "",
                r.engine_smoh or "", r.bucket,
                r.url, ",".join(r.avionics_hits)
            ])


# ============================================================
# Main
# ============================================================
def main():
    if len(sys.argv) < 2:
        print('Usage: aircraft_price_scan.py "PA-28" | "C172" [--save]')
        sys.exit(1)

    family_arg = sys.argv[1].lower()
    save = "--save" in sys.argv

    if "pa-28" in family_arg or "cherokee" in family_arg or "arrow" in family_arg:
        family = "Piper PA-28 / Cherokee / Arrow"
        listings = scrape_family(family, pa28_sources)

    elif "172" in family_arg:
        family = "Cessna 172 Family"
        listings = scrape_family(family, c172_sources)

    else:
        print("Supported families:")
        print("  PA-28 / Cherokee / Arrow")
        print("  Cessna 172")
        sys.exit(1)

    print(f"\n=== Aircraft Price Scan — {family} ===\n")
    print(f"Total raw listings: {len(listings)}")

    prices = [l.price_usd for l in listings]
    if not prices:
        print("No valid listings with prices found.")
        return

    print(f"Price range: ${min(prices):,} – ${max(prices):,}")
    print(f"Median:      ${median_safe(prices):,}")
    print(f"Mean:        ${mean_safe(prices):,}\n")

    print("Sample listings:")
    for l in listings[:8]:
        print(
            f"- [{l.site}] {l.year or '????'} – ${l.price_usd:,} – {l.bucket}\n"
            f"  {l.title[:110]}...\n"
            f"  {l.url}\n"
        )

    if save:
        out = DATA_DIR / "aircraft_prices.csv"
        export_csv(listings, out)
        snap = ARCHIVE_DIR / f"aircraft_prices_{family.replace(' ','_')}.csv"
        export_csv(listings, snap)
        print(f"\nCSV saved → {out}")
        print(f"Snapshot archived → {snap}")


if __name__ == "__main__":
    main()
