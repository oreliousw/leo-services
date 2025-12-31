#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# Project: Leo Services
# File: aircraft_price_scan.py
# Version: v0.9.2 — 2025-12-31
# Change: Bot-aware scrapers; added AeroTrader + Aircraft.com RSS;
#         skip blocked/gated pages; keep flyable-only comps
# Note: Bump Version + Change when modifying runtime behavior

Aircraft Price Scan — Market Comparison Tool
"""

import re
import csv
import sys
import json
import time
import pathlib
import statistics
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

BASE_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ARCHIVE_DIR = DATA_DIR / "archives"
SCRAPE_DEBUG = BASE_DIR / "scrape_debug"

for d in (DATA_DIR, ARCHIVE_DIR, SCRAPE_DEBUG):
    d.mkdir(parents=True, exist_ok=True)

# ============================================================
# Dataclass
# ============================================================
@dataclass
class AircraftListing:
    site: str
    model_query: str
    title: str
    year: Optional[int]
    price_usd: Optional[int]
    total_time: Optional[int]
    engine_smoh: Optional[int]
    location: Optional[str]
    url: str
    avionics_hits: List[str]
    raw_text: str
    bucket: str = "vfr_basic"


# ============================================================
# Regex filters
# ============================================================
PRICE_RE = re.compile(r"\$(?:\s*|\b)([\d,]+)")
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")
HOURS_RE = re.compile(r"\b(\d{3,5})\s*(?:TT|TTAF|TOTAL TIME)", re.I)
SMOH_RE = re.compile(r"\b(\d{2,5})\s*(?:SMOH|S\.M\.O\.H\.)", re.I)

PROJECT_TERMS = [
    "project", "no engine", "no prop", "salvage",
    "needs rebuild", "parts only", "damaged",
    "core airframe", "hangar rash"
]

def parse_price(t): m=PRICE_RE.search(t);return int(m.group(1).replace(",","")) if m else None
def parse_year(t): m=YEAR_RE.search(t);return int(m.group(1)) if m else None
def parse_hours(t): m=HOURS_RE.search(t);return int(m.group(1)) if m else None
def parse_smoh(t): m=SMOH_RE.search(t);return int(m.group(1)) if m else None
def is_project_text(t): 
    tl = t.lower()
    return any(p in tl for p in PROJECT_TERMS)


# ============================================================
# Avionics classifier
# ============================================================
AVIONICS_KEYWORDS = {
    "g5": ["g5","garmin g5"],
    "g3x": ["g3x"],
    "ifd": ["avidyne ifd","ifd440","ifd540"],
    "gtn": ["gtn650","gtn750"],
    "430w": ["430w","gns 430w"],
    "530w": ["530w","gns 530w"],
    "adsb": ["ads-b","adsb","skybeacon","uavionix"],
    "ap": ["autopilot","stech","truetrak","kfc","kapp"],
}

def detect_avionics(text:str)->List[str]:
    t=text.lower()
    return [k for k,v in AVIONICS_KEYWORDS.items() if any(x in t for x in v)]

def derive_bucket(hits:List[str])->str:
    h=set(hits)
    if "g5" in h or "g3x" in h:
        return "glass_ap" if "ap" in h else "ifr_modern"
    if any(x in h for x in ("430w","530w","gtn","ifd")):
        return "ifr_legacy_gps"
    return "vfr_basic"


# ============================================================
# HTTP + Bot-awareness
# ============================================================
UAS = [
    "Mozilla/5.0 (Leo-PriceScan/1.0)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh) Safari/605.1.15",
]

BLOCK_PATTERNS = [
    "pardon our interruption",
    "captcha",
    "distil",
    "bot detected",
    "cloudflare",
    "internal server error"
]

def looks_blocked(html:str)->bool:
    t = html.lower()
    return any(p in t for p in BLOCK_PATTERNS)

def fetch_html(url:str, save_name:str=None, retries:int=2)->Optional[str]:
    for i, ua in enumerate(UAS[:retries+1]):
        try:
            r=requests.get(url,headers={"User-Agent":ua,"Accept-Language":"en-US"},timeout=20)
            if r.status_code>=500:
                print(f"[WARN] {url} -> HTTP {r.status_code} (server error, UA idx {i})")
                continue
            if r.status_code==403:
                print(f"[WARN] {url} -> HTTP 403, retrying with different UA...")
                time.sleep(1.2)
                continue
            html=r.text
            if save_name:
                (SCRAPE_DEBUG/save_name).write_text(html[:500000],encoding="utf-8",errors="ignore")
            if looks_blocked(html):
                print(f"[INFO] {url} appears gated/bot-blocked — skipping source")
                return None
            return html
        except Exception as e:
            print(f"[WARN] Fetch failed {url}: {e}")
    return None


# ============================================================
# Listing builder + project filter
# ============================================================
def make_listing(site,query,text,url)->Optional[AircraftListing]:
    if is_project_text(text):
        return None
    price = parse_price(text)
    if not price:
        return None
    hits=detect_avionics(text)
    return AircraftListing(
        site=site,
        model_query=query,
        title=text[:160],
        year=parse_year(text),
        price_usd=price,
        total_time=parse_hours(text),
        engine_smoh=parse_smoh(text),
        location=None,
        url=url,
        avionics_hits=hits,
        raw_text=text,
        bucket=derive_bucket(hits),
    )


# ============================================================
# CLEAN SOURCES ONLY
# ============================================================

# --- AeroTrader (JSON-ish page payload) ---
def fetch_aerotrader(query:str)->List[AircraftListing]:
    q = quote_plus(query)
    url = f"https://www.autotrader.com/marketplace/buy/aircraft?searchTerm={q}"
    html = fetch_html(url,"aerotrader_latest.html")
    if not html: return []
    out=[]
    # crude JSON scrape for listing cards:
    for m in re.finditer(r'"listingTitle":"(.*?)".*?"price":\s*"?([\d,]+)', html, re.S):
        text = f"{m.group(1)} ${m.group(2)}"
        l = make_listing("AeroTrader",query,text,url)
        if l: out.append(l)
    return out


# --- Aircraft.com RSS (stable XML feed) ---
def fetch_aircraft_com(query:str)->List[AircraftListing]:
    url = "https://www.aircraft.com/rss/for-sale.xml"
    html = fetch_html(url,"aircraftcom_latest.xml")
    if not html: return []
    soup = BeautifulSoup(html,"xml")
    out=[]
    for item in soup.select("item"):
        t = " ".join(item.get_text(" ",strip=True).split())
        if query.lower().split()[0] not in t.lower():
            continue
        link=item.find("link").get_text(strip=True) if item.find("link") else url
        l = make_listing("Aircraft.com",query,t,link)
        if l: out.append(l)
    return out


# --- Trade-A-Plane (only when real cards present) ---
def fetch_trade_a_plane(query:str, routed:bool)->List[AircraftListing]:
    if routed:
        url="https://www.trade-a-plane.com/search?make=PIPER&model=CHEROKEE+140%2F160&s-type=aircraft" \
            if "piper" in query.lower() else \
            "https://www.trade-a-plane.com/search?make=CESSNA&model=172&s-type=aircraft"
    else:
        q=quote_plus(query)
        url=f"https://www.trade-a-plane.com/search?s-type=aircraft&term={q}"
    html=fetch_html(url,"tradeaplane_latest.html")
    if not html: return []
    soup=BeautifulSoup(html,"html.parser")
    out=[]
    for card in soup.select("div.card, div.search-card, article, li"):
        text=" ".join(card.get_text(' ',strip=True).split())
        l=make_listing("Trade-A-Plane",query,text,url)
        if l: out.append(l)
    return out


# ============================================================
# Routing logic
# ============================================================
def detect_mode(query:str)->Tuple[str,bool]:
    q=query.lower()
    if "cherokee" in q or "pa-28" in q:
        return "routed-family", True
    if "172" in q and "cessna" in q:
        return "routed-family", True
    return "keyword-search", False


# ============================================================
# Stats / CSV
# ============================================================
def median_safe(xs):return int(statistics.median(xs)) if xs else None
def mean_safe(xs):return int(statistics.mean(xs)) if xs else None

def export_csv(rows:List[AircraftListing],path:pathlib.Path):
    with path.open("w",newline="") as f:
        w=csv.writer(f)
        w.writerow(["site","model_query","title","year","price_usd",
                    "total_time","engine_smoh","location","url",
                    "bucket","avionics_hits"])
        for r in rows:
            w.writerow([
                r.site,r.model_query,r.title,r.year or "",
                r.price_usd or "",r.total_time or "",
                r.engine_smoh or "",r.location or "",
                r.url,r.bucket,",".join(r.avionics_hits)
            ])


# ============================================================
# Main
# ============================================================
def main():
    if len(sys.argv)<2:
        print('Usage: aircraft_price_scan.py "Piper Cherokee 140" [--save] [--debug-sources]')
        sys.exit(1)

    query=sys.argv[1]
    save="--save" in sys.argv
    debug="--debug-sources" in sys.argv

    mode,routed = detect_mode(query)

    print("Mode:",mode,"\n")
    print("=== Aircraft Price Scan ===")
    print(f"Model query: {query}\n")

    listings:List[AircraftListing]=[]
    source_status={}

    def collect(name, fn):
        res=[]
        try:
            res = fn()
        except Exception as e:
            print(f"[WARN] {name} fetch error: {e}")
            res=[]
        listings.extend(res)
        source_status[name] = dict(attempted=True,count=len(res))

    # Clean sources first
    collect("AeroTrader", lambda: fetch_aerotrader(query))
    collect("Aircraft.com", lambda: fetch_aircraft_com(query))
    collect("Trade-A-Plane", lambda: fetch_trade_a_plane(query,routed))

    print(f"Total raw listings collected: {len(listings)}")

    listings=[l for l in listings if l and l.price_usd]

    if not listings:
        print("No valid flyable listings with prices found.\n")
        print("\n=== Source Status ===")
        for k,v in source_status.items():
            mark = "✓" if v["count"]>0 else "…"
            print(f"{mark} {k:14s} attempted={v['attempted']} count={v['count']}")
        return

    print(f"Total valid aircraft listings: {len(listings)}\n")

    prices=[l.price_usd for l in listings]
    print(
        f"Overall price range: ${min(prices):,} – ${max(prices):,}\n"
        f"Overall median:      ${median_safe(prices):,}\n"
        f"Overall mean:        ${mean_safe(prices):,}\n"
    )

    print("Sample listings:")
    for l in listings[:8]:
        print(
            f"- [{l.site}] {l.year or '????'} – ${l.price_usd:,} – {l.bucket}\n"
            f"  {l.title[:110]}...\n"
            f"  {l.url}\n"
        )

    print("\n=== Bucket Medians ===")
    buckets:Dict[str,List[AircraftListing]]={}
    for l in listings:
        buckets.setdefault(l.bucket,[]).append(l)

    for name in ["vfr_basic","ifr_legacy_gps","ifr_modern","glass_ap"]:
        xs=[x.price_usd for x in buckets.get(name,[]) if x.price_usd]
        if xs:
            print(f"{name:16s} n={len(xs):<3} median=${median_safe(xs):,}  mean=${mean_safe(xs):,}")
        else:
            print(f"{name:16s} n=0")

    print("\n=== Source Status ===")
    for k,v in source_status.items():
        mark = "✓" if v["count"]>0 else "…"
        print(f"{mark} {k:14s} attempted={v['attempted']} count={v['count']}")

    if save:
        out=DATA_DIR / "aircraft_prices.csv"
        export_csv(listings,out)
        print(f"\nCSV saved → {out}")
        snap=ARCHIVE_DIR / f"aircraft_prices_{query.replace(' ','_')}.csv"
        export_csv(listings,snap)
        print(f"Snapshot archived → {snap}")


if __name__=="__main__":
    main()
