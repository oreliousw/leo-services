#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# Project: Leo Services
# File: aircraft_price_scan.py
# Version: v1.1.10 — 2026-01-01
# Change: Added full support for "C172" / Cessna 172 / Skyhawk family
#         - Dedicated TAP and AFS URLs
#         - Keyword matching for C172 listings
#         - Updated main() to route to correct family
#         Looser FB filter unchanged from v1.1.9
# Note: System deps: ungoogled-chromium + manual chromedriver in /usr/local/bin/chromedriver
#       venv deps: requests, beautifulsoup4, selenium
Aircraft Price Scan — Family-Locked Market Comparison Tool
"""
import re
import sys
import csv
import pathlib
import statistics
import time
import random
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

# For Facebook Marketplace (optional / Selenium)
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    print("[WARN] Selenium not installed → Facebook Marketplace support disabled.")
    print("[INFO] To enable: pip install selenium (inside venv)")

BASE_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ARCHIVE_DIR = DATA_DIR / "archives"
IMPORT_DIR = BASE_DIR / "import"  # For manual HTML saves
DATA_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
IMPORT_DIR.mkdir(parents=True, exist_ok=True)

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
    m = PRICE_RE.search(text)
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
# HTTP with UA rotation + delays (for public sites)
# ============================================================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

def fetch_html(url, retries=5):
    for attempt in range(retries):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Referer": "https://www.google.com/",
        }
        try:
            time.sleep(random.uniform(5, 10))
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code != 200:
                print(f"[WARN] {url} → HTTP {r.status_code} (attempt {attempt+1})")
                continue
            print(f"[DEBUG] Fetched {url}: {len(r.text)} chars")
            return r.text
        except Exception as e:
            print(f"[WARN] Fetch failed {url}: {e} (attempt {attempt+1})")
            time.sleep(5)
    print(f"[ERROR] All retries failed for {url}")
    return None

# ============================================================
# Manual import fallback
# ============================================================
def load_from_html(path: pathlib.Path, family: str) -> List[AircraftListing]:
    listings = []
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup.select("div.listing, article, div.card, .listing-item, div.x1yztbdb"):
            text = " ".join(tag.get_text(" ", strip=True).split())
            l = make_listing("Manual Import", family, text, f"file://{path.name}")
            if l:
                listings.append(l)
    except Exception as e:
        print(f"[WARN] HTML import failed for {path}: {e}")
    return listings

def try_import_folder(family: str) -> List[AircraftListing]:
    listings = []
    if not IMPORT_DIR.exists() or not any(IMPORT_DIR.iterdir()):
        return listings
    print(f"[INFO] Checking manual imports in {IMPORT_DIR}...")
    for path in IMPORT_DIR.glob("*.html"):
        print(f"[INFO] Importing {path.name}")
        listings.extend(load_from_html(path, family))
    return listings

# ============================================================
# Listing builder
# ============================================================
def make_listing(site, family, text, url):
    t_lower = text.lower()
    if any(word in t_lower for word in ["range", "under $", "choose a", "to $"]):
        return None
    if is_project(text):
        return None
    price = parse_price(text)
    if not price or price < 20000:
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
# --------- Trade-A-Plane sources ----------
def pa28_sources_tradeaplane():
    return [
        ("Trade-A-Plane PA-28",
         "https://www.trade-a-plane.com/search?make=PIPER&model=CHEROKEE+140%2F160&s-type=aircraft",
         "div.result, div.listing, article"),
    ]

def c172_sources_tradeaplane():
    return [
        ("Trade-A-Plane C172",
         "https://www.trade-a-plane.com/search?make=CESSNA&model=172&s-type=aircraft",
         "div.result, div.listing, article"),
    ]

# --------- AircraftForSale.com sources ----------
def pa28_sources_afs():
    return [
        ("AircraftForSale PA-28",
         "https://aircraftforsale.com/aircraft/single-engine-piston/piper/pa-28-cherokee",
         "div.listing, article.listing, div.card, .listing-item, div.result"),
    ]

def c172_sources_afs():
    return [
        ("AircraftForSale C172",
         "https://aircraftforsale.com/aircraft/single-engine-piston/cessna/172-skyhawk",
         "div.listing, article.listing, div.card, .listing-item, div.result"),
    ]

# --------- Facebook Marketplace (same for both families) ----------
def facebook_marketplace_sources():
    return [
        ("Facebook Marketplace",
         "https://www.facebook.com/marketplace/category/aircraft?query=piper%20pa-28%20cherokee",
         "div.x1yztbdb.x1n2onr6.xh8yej3, div.x1lliihq, article"),
    ]

def scrape_facebook_marketplace(family):
    if not SELENIUM_AVAILABLE:
        print("[WARN] Facebook Marketplace skipped: install selenium inside venv.")
        return []

    listings = []
    for site, url, sel in facebook_marketplace_sources():
        print(f"[INFO] Attempting {site} (headless Selenium)...")
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.binary_location = "/usr/bin/ungoogled-chromium"
        options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")

        try:
            service = Service("/usr/local/bin/chromedriver")
            driver = webdriver.Chrome(service=service, options=options)
            driver.get(url)
            time.sleep(10)
            html = driver.page_source
            soup = BeautifulSoup(html, "html.parser")
            results = soup.select(sel)
            print(f"[DEBUG] Found {len(results)} potential FB listings")
            for item in results:
                text = " ".join(item.get_text(" ", strip=True).split())
                a = item.find("a", href=True)
                href = url
                if a and a["href"]:
                    href = a["href"]
                    if href.startswith("/"):
                        href = "https://www.facebook.com" + href
                t_lower = text.lower()
                # Loosened filter
                if "piper" not in t_lower and "cherokee" not in t_lower:
                    continue
                l = make_listing(site, family, text, href)
                if l:
                    listings.append(l)
                    print(f"[+] FB hit: {l.title[:80]}... ${l.price_usd:,}")
        except Exception as e:
            print(f"[WARN] Facebook Marketplace failed: {e}")
            print("[INFO] Tip: Log in manually, search, save page as HTML → drop in import/ folder")
        finally:
            if 'driver' in locals():
                driver.quit()

    if not listings:
        print("[INFO] No Facebook listings captured — use manual import fallback.")
    return listings

# --------- Generic scraper helper ----------
from bs4.element import Tag

def scrape_generic(site, family, url, sel):
    listings = []
    html = fetch_html(url)
    if not html:
        return listings
    soup = BeautifulSoup(html, "html.parser")
    results = soup.select(sel)
    print(f"[INFO] Scraping {site} with sel '{sel}'... found {len(results)} elements")
    for card in results:
        if not isinstance(card, Tag):
            continue
        text = " ".join(card.get_text(" ", strip=True).split())
        l = make_listing(site, family, text, url)
        if l:
            listings.append(l)
    return listings

# --------- Family-specific scraper dispatcher ----------
def scrape_family(family):
    listings = []
    if "pa-28" in family.lower() or "cherokee" in family.lower() or "arrow" in family.lower():
        # PA-28 family
        for site, url, sel in pa28_sources_tradeaplane():
            listings += scrape_generic(site, family, url, sel)
        for site, url, sel in pa28_sources_afs():
            listings += scrape_generic(site, family, url, sel)
    elif "c172" in family.lower() or "cessna 172" in family.lower() or "skyhawk" in family.lower():
        # C172 family
        for site, url, sel in c172_sources_tradeaplane():
            listings += scrape_generic(site, family, url, sel)
        for site, url, sel in c172_sources_afs():
            listings += scrape_generic(site, family, url, sel)
    else:
        print("[WARN] Unknown family — no specific sources found.")
    # Common FB attempt for both
    listings += scrape_facebook_marketplace(family)
    # Manual imports always last
    listings += try_import_folder(family)
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
        print('Usage: aircraft_price_scan.py "PA-28" or "C172" [--save]')
        sys.exit(1)
    family_arg = sys.argv[1].lower()
    save = "--save" in sys.argv
    if "pa-28" in family_arg or "cherokee" in family_arg or "arrow" in family_arg:
        family = "Piper PA-28 / Cherokee / Arrow"
    elif "c172" in family_arg or "cessna 172" in family_arg or "skyhawk" in family_arg:
        family = "Cessna 172 / Skyhawk"
    else:
        print("Supported families:")
        print(" - PA-28 / Cherokee / Arrow")
        print(" - C172 / Cessna 172 / Skyhawk")
        sys.exit(1)
    listings = scrape_family(family)
    print(f"\n=== Aircraft Price Scan — {family} ===\n")
    print(f"Total raw listings: {len(listings)}")
    prices = [l.price_usd for l in listings]
    if not prices:
        print("No valid listings with prices found.")
        print("[TIP] Save search results as HTML to import/ folder and rerun.")
        return
    print(f"Core price range: ${min(prices):,} – ${max(prices):,}")
    print(f"Core median: ${median_safe(prices):,}")
    print(f"Core mean: ${mean_safe(prices):,}\n")
    print("Sample core listings:")
    for l in listings[:8]:
        print(
            f"- [{l.site}] {l.year or '????'} – ${l.price_usd:,} – {l.bucket}\n"
            f" {l.title[:110]}...\n"
            f" {l.url}\n"
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