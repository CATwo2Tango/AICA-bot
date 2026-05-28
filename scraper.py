"""
scraper.py  —  Scrapes https://ai.icai.org/aica.php
Returns a list of batch dicts with:
    batch_id, batch_name, location, dates, registration_open,
    detail_url, available_seats, batch_limit, venue

Registration is considered OPEN only when ALL of:
  1. No reg_close image on the main listing page
  2. Available seats > 0  (from the detail page)
  3. No "Registration Closed" text on the detail page
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
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://ai.icai.org/",
}

# Simple in-memory cache
_cache: dict = {}
CACHE_TTL = 25 * 60  # 25 minutes


# ─────────────────────────────────────────────────────────────────────────────
# Internal fetch
# ─────────────────────────────────────────────────────────────────────────────

def _fetch(url: str, timeout: int = 15) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return None


def _parse_batch_id(detail_url: str) -> str:
    m = re.search(r"id=(\d+)", detail_url)
    return m.group(1) if m else detail_url


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Parse main listing page (fast, no seat info)
# ─────────────────────────────────────────────────────────────────────────────

def get_all_batches(force: bool = False) -> list[dict]:
    """
    Scrape the main AICA page.
    Registration flag here is a PRELIMINARY check (reg_close image only).
    For accurate seat-based status, use get_all_batches_with_seats().
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

    for h3 in soup.find_all("h3"):
        link = h3.find("a", href=True)
        if not link or "course_details.php" not in link["href"]:
            continue

        batch_name_raw = link.get_text(strip=True)
        detail_url = BASE_URL + "/" + link["href"].lstrip("/")
        batch_id = _parse_batch_id(detail_url)
        location, dates = _parse_name(batch_name_raw)

        # ── Registration check from main page ──────────────────────────────
        # ICAI shows a "reg_close.png" image overlay when registration is closed
        prev_a = h3.find_previous_sibling("a")
        reg_closed_on_main = False
        if prev_a:
            for img in prev_a.find_all("img"):
                src = img.get("src", "").lower()
                if "reg_close" in src:
                    reg_closed_on_main = True
                    break

        batches.append({
            "batch_id":          batch_id,
            "batch_name":        batch_name_raw,
            "location":          location,
            "dates":             dates,
            "registration_open": not reg_closed_on_main,  # preliminary
            "detail_url":        detail_url,
            "available_seats":   None,   # populated by get_batch_details()
            "batch_limit":       None,
            "venue":             "",
        })

    logger.info(f"Scraped {len(batches)} batches from ICAI main page")
    _cache["batches"] = batches
    _cache["ts"] = now
    return batches


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Parse individual detail page (has seat count + confirmed reg status)
# ─────────────────────────────────────────────────────────────────────────────

def get_batch_details(detail_url: str) -> dict:
    """
    Fetch individual batch detail page.
    Returns: batch_limit, available_seats, venue, registration_open (definitive)
    """
    html = _fetch(detail_url)
    if not html:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text(" ", strip=True)
    details = {}

    # ── Batch Limit ────────────────────────────────────────────────────────
    m = re.search(r"Batch\s*Limit[:\s]*(\d+)", full_text, re.IGNORECASE)
    if m:
        details["batch_limit"] = int(m.group(1))

    # ── Available Seats ────────────────────────────────────────────────────
    m = re.search(r"Available\s*Seat[s]?[:\s]*(\d+)", full_text, re.IGNORECASE)
    if m:
        details["available_seats"] = int(m.group(1))

    # ── Venue ──────────────────────────────────────────────────────────────
    m = re.search(
        r"Venue[:\s]*(.+?)(?:Please\s*select|Registration|©|$)",
        full_text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        details["venue"] = " ".join(m.group(1).split())[:300]

    # ── Definitive registration status ─────────────────────────────────────
    # A batch is OPEN only if:
    #   (a) seats > 0, AND
    #   (b) no "Registration Closed" / "reg_close" on the page
    seats = details.get("available_seats")

    closed_text = bool(re.search(
        r"registration\s*(is\s*)?(closed|full|over|ended)",
        full_text,
        re.IGNORECASE,
    ))

    # Check for reg_close image on detail page too
    closed_img = any(
        "reg_close" in (img.get("src", "").lower())
        for img in soup.find_all("img")
    )

    if seats is not None:
        # If we have seat count, use it as the primary signal
        details["registration_open"] = (seats > 0) and not closed_text and not closed_img
    else:
        # No seat count found — fall back to text/image signals only
        details["registration_open"] = not closed_text and not closed_img

    return details


# ─────────────────────────────────────────────────────────────────────────────
# Combined — main page + detail pages (accurate but slower)
# ─────────────────────────────────────────────────────────────────────────────

def get_all_batches_with_seats() -> list[dict]:
    """
    Fetches main page + every detail page.
    registration_open here is ACCURATE (seat-count aware).
    Use this for /seats and /open commands.
    Politely throttled to avoid hammering ICAI servers.
    """
    batches = get_all_batches()
    enriched = []
    for b in batches:
        det = get_batch_details(b["detail_url"])
        merged = {**b, **det}   # detail page values OVERRIDE main page values
        enriched.append(merged)
        time.sleep(0.3)
    logger.info(f"Enriched {len(enriched)} batches with seat data")
    return enriched


# ─────────────────────────────────────────────────────────────────────────────
# Helper — parse batch name into location + dates
# ─────────────────────────────────────────────────────────────────────────────

def _parse_name(name: str) -> tuple[str, str]:
    """
    From a raw batch name string, extract location and dates.
    Handles formats like:
      "Batch713 - Mumbai: 25,26,27 May 2026"
      "Batch781-PUNE-9,10,11 JUNE 2026"
      "Batch768-AHMEDABAD-16,17,18 JUNE 2026"
    """
    # Strip the "BatchNNN -" or "BatchNNN-" prefix
    name_clean = re.sub(r"^Batch\d+\s*[-:]\s*", "", name, flags=re.IGNORECASE).strip()

    # Try "LOCATION: dates" or "LOCATION - dates"
    m = re.match(r"^([A-Za-z\s().]+?)[-:]\s*(.+)$", name_clean)
    if m:
        return m.group(1).strip().title(), m.group(2).strip().title()

    # Fallback: split on dash
    parts = re.split(r"[-–]", name_clean, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip().title(), parts[1].strip().title()

    return name_clean.title(), "See link"
