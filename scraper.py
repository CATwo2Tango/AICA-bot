"""
scraper.py  —  Scrapes https://ai.icai.org/aica.php
Returns a list of batch dicts with:
    batch_id, batch_name, location, dates, registration_open,
    detail_url, [available_seats, batch_limit, venue]
"""

import re
import time
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://ai.icai.org"
MAIN_URL = f"{BASE_URL}/aica.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# Simple in-memory cache
_cache: dict = {}
CACHE_TTL = 25 * 60  # 25 minutes


def _fetch(url: str, timeout: int = 15) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return None


def _parse_batch_id(detail_url: str) -> str:
    """Extract numeric id from course_details.php?id=XXX"""
    m = re.search(r"id=(\d+)", detail_url)
    return m.group(1) if m else detail_url


def get_all_batches(force: bool = False) -> list[dict]:
    """
    Scrape the main AICA page and return list of batch dicts.
    Uses cache unless force=True.
    """
    now = time.time()
    if not force and "batches" in _cache:
        if now - _cache["ts"] < CACHE_TTL:
            logger.info("Returning cached batches")
            return _cache["batches"]

    html = _fetch(MAIN_URL)
    if not html:
        return _cache.get("batches", [])

    soup = BeautifulSoup(html, "html.parser")
    batches = []

    # Each batch is an <a> tag wrapping images + an <h3> heading
    # Structure:  <a href="course_details.php?id=XXX">
    #               <img ...reg_close...>  (optional — if present = closed)
    #               <img ...banner...>
    #             </a>
    #             <h3><a href="course_details.php?id=XXX">Batch713 - Mumbai: ...</a></h3>

    # Find all h3 headings that contain a batch link
    for h3 in soup.find_all("h3"):
        link = h3.find("a", href=True)
        if not link or "course_details.php" not in link["href"]:
            continue

        batch_name_raw = link.get_text(strip=True)
        detail_url = BASE_URL + "/" + link["href"].lstrip("/")
        batch_id = _parse_batch_id(detail_url)

        # Parse batch name → extract location & dates
        # Examples:
        #   "Batch713 - Mumbai: 25,26,27 May 2026"
        #   "Batch781-PUNE-9,10,11 JUNE 2026"
        location, dates = _parse_name(batch_name_raw)

        # Check reg_close image: look in the preceding sibling <a> tag
        prev_a = h3.find_previous_sibling("a")
        reg_open = True
        if prev_a:
            imgs = prev_a.find_all("img")
            for img in imgs:
                src = img.get("src", "")
                if "reg_close" in src:
                    reg_open = False
                    break

        batches.append({
            "batch_id": batch_id,
            "batch_name": batch_name_raw,
            "location": location,
            "dates": dates,
            "registration_open": reg_open,
            "detail_url": detail_url,
        })

    logger.info(f"Scraped {len(batches)} batches from ICAI")
    _cache["batches"] = batches
    _cache["ts"] = now
    return batches


def get_batch_details(detail_url: str) -> dict:
    """
    Fetch individual batch detail page and extract:
    batch_limit, available_seats, date, time, venue
    """
    html = _fetch(detail_url)
    if not html:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    details = {}

    full_text = soup.get_text(" ", strip=True)

    # Batch Limit
    m = re.search(r"Batch Limit[:\s]*(\d+)", full_text, re.IGNORECASE)
    if m:
        details["batch_limit"] = int(m.group(1))

    # Available Seat
    m = re.search(r"Available Seat[:\s]*(\d+)", full_text, re.IGNORECASE)
    if m:
        details["available_seats"] = int(m.group(1))

    # Date
    m = re.search(r"Date[:\s]*([\d\-]+)", full_text, re.IGNORECASE)
    if m:
        details["detail_date"] = m.group(1).strip()

    # Venue (everything after "Venue:" up to the next known label or end)
    m = re.search(r"Venue[:\s]*(.+?)(?:Please select|Registration|©|$)", full_text, re.IGNORECASE | re.DOTALL)
    if m:
        details["venue"] = " ".join(m.group(1).split())[:250]  # trim to 250 chars

    return details


def get_all_batches_with_seats() -> list[dict]:
    """
    Fetch main page + detail page for every batch (slow — use sparingly).
    Adds available_seats, batch_limit, venue to each batch dict.
    """
    batches = get_all_batches()
    enriched = []
    for b in batches:
        det = get_batch_details(b["detail_url"])
        enriched.append({**b, **det})
        time.sleep(0.3)   # be polite to ICAI servers
    return enriched


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _parse_name(name: str) -> tuple[str, str]:
    """
    From a raw batch name string, extract location and dates.
    Handles both dash-separated and colon-separated formats.
    """
    # Remove "Batch713 " or "Batch713-" prefix
    name_clean = re.sub(r"^Batch\d+\s*[-:]\s*", "", name, flags=re.IGNORECASE).strip()

    # Try "LOCATION - dates" or "LOCATION: dates"
    m = re.match(r"^([A-Za-z\s()]+?)[-:]\s*(.+)$", name_clean)
    if m:
        location = m.group(1).strip().title()
        dates = m.group(2).strip().title()
        return location, dates

    # Fallback: last segment is dates (contains digits)
    parts = re.split(r"[-–]", name_clean)
    if len(parts) >= 2:
        return parts[0].strip().title(), " ".join(parts[1:]).strip().title()

    return name_clean, "See link"
