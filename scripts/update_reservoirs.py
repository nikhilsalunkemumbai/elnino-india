
    
"""
update_reservoirs.py
Fetches India major reservoir live storage from CWC RSMS and fallback sources.

═══════════════════════════════════════════════════════════════════════════════
SESSION SUMMARY INTEGRATION (session_summary.txt — 18 Jun 2026)
═══════════════════════════════════════════════════════════════════════════════
The RSMS (Reservoir Storage Monitoring System) at rsms.cwc.gov.in is the
AUTHORITATIVE CWC reservoir data source. Session findings:

  • Bulletin URL pattern:
      https://rsms.cwc.gov.in/admin/storage/bulletins/bulletin-DD-MM-YYYY-XXX.pdf
  • Latest confirmed:  bulletin-18-06-2026-105.pdf  (Thursday 18 Jun 2026)
  • Sequence:         increments by 1 each week (105 = week anchor)
  • Cadence:          every Thursday
  • Backend:          Laravel/Lumen 11.1.0 at rsms.cwc.gov.in/admin/
  • Frontend:         Angular (dynamic — HTML scraping won't work)
  • API:              not yet discovered (Angular network-tab inspection needed)

RSMS PDF is a better source than CWC general-site PDFs because RSMS is the
dedicated system built specifically for reservoir monitoring.

═══════════════════════════════════════════════════════════════════════════════
FOUR-TIER RETRIEVAL STRATEGY
═══════════════════════════════════════════════════════════════════════════════

Tier 1 — RSMS Laravel API  (rsms.cwc.gov.in)
  Attempt to discover the actual backend JSON API used by the Angular frontend.
  Tries a set of plausible Laravel/Lumen route patterns derived from the known
  admin base URL. If found, returns clean structured JSON. Session summary
  recommends this as the most robust approach.

Tier 2 — RSMS PDF Bulletin  (rsms.cwc.gov.in)
  Direct PDF download using the confirmed URL pattern from the session summary.
  Sequence number is computed from the known anchor (105 = 18 Jun 2026) plus
  weeks elapsed. Tries current week and up to 3 weeks back for publication lag.
  Parsed with pdfplumber.

Tier 3 — India-WRIS JSON API  (indiawris.gov.in)
  CWC/NHP/World Bank system. REST JSON API.

Tier 4 — data.gov.in OGD Platform
  Open Government Data. Last-resort; may be stale by days.

Output: data/reservoirs.csv
  Columns: date, name, state, capacity_bcm, live_storage_bcm,
           live_storage_pct, ten_yr_avg_pct, deficit_pct, status, source
"""

import csv
import io
import json
import re
import requests
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# RSMS anchor — from session_summary.txt
# ─────────────────────────────────────────────────────────────────────────────
RSMS_BASE_URL      = "https://rsms.cwc.gov.in"
RSMS_BULLETIN_BASE = f"{RSMS_BASE_URL}/admin/storage/bulletins"
RSMS_API_BASE      = f"{RSMS_BASE_URL}/admin"
RSMS_ANCHOR_DATE   = date(2026, 6, 18)   # confirmed date of bulletin #105
RSMS_ANCHOR_SEQ    = 105                  # bulletin sequence number on anchor date

# Plausible Laravel/Lumen API routes to probe (Approach A from session summary)
# Laravel resource controllers follow RESTful conventions; Lumen uses similar patterns
RSMS_API_CANDIDATES = [
    "/api/bulletins",
    "/api/bulletin",
    "/api/bulletin-list",
    "/api/reservoirs",
    "/api/reservoir-storage",
    "/api/storage",
    "/api/v1/bulletins",
    "/api/v1/reservoir-storage",
    "/bulletins",
    "/reservoir-storage",
    "/storage/bulletins",
]

# ─────────────────────────────────────────────────────────────────────────────
# Known reservoirs — capacity reference (CWC Annual Report 2023-24)
# ─────────────────────────────────────────────────────────────────────────────
KNOWN_RESERVOIRS = [
    {"name": "Bhakra (Gobind Sagar)",  "state": "Punjab/HP",       "capacity_bcm": 9.34},
    {"name": "Hirakud",                "state": "Odisha",           "capacity_bcm": 8.14},
    {"name": "Nagarjunasagar",         "state": "Telangana/AP",     "capacity_bcm": 11.56},
    {"name": "Srisailam",              "state": "Telangana/AP",     "capacity_bcm": 8.72},
    {"name": "Indira Sagar (Narmada)", "state": "Madhya Pradesh",   "capacity_bcm": 12.22},
    {"name": "Tungabhadra",            "state": "Karnataka",        "capacity_bcm": 3.70},
    {"name": "Koyna",                  "state": "Maharashtra",      "capacity_bcm": 2.80},
    {"name": "Tehri",                  "state": "Uttarakhand",      "capacity_bcm": 3.54},
    {"name": "Ukai",                   "state": "Gujarat",          "capacity_bcm": 7.42},
    {"name": "Almatti",                "state": "Karnataka",        "capacity_bcm": 3.48},
    {"name": "Sardar Sarovar",         "state": "Gujarat",          "capacity_bcm": 9.46},
    {"name": "Maithan",                "state": "Jharkhand",        "capacity_bcm": 1.44},
    {"name": "Panchet",                "state": "Jharkhand",        "capacity_bcm": 1.47},
    {"name": "Rihand",                 "state": "Uttar Pradesh",    "capacity_bcm": 10.60},
    {"name": "Rana Pratap Sagar",      "state": "Rajasthan",        "capacity_bcm": 3.41},
    {"name": "Idukki",                 "state": "Kerala",           "capacity_bcm": 1.99},
    {"name": "Supa",                   "state": "Karnataka",        "capacity_bcm": 7.10},
    {"name": "Pong (Beas)",            "state": "Himachal Pradesh", "capacity_bcm": 8.57},
    {"name": "Tawa",                   "state": "Madhya Pradesh",   "capacity_bcm": 2.00},
    {"name": "Bhadar",                 "state": "Gujarat",          "capacity_bcm": 0.30},
]
CAPACITY_LOOKUP = {r["name"]: r for r in KNOWN_RESERVOIRS}


def storage_status(live_pct: float) -> str:
    if live_pct < 25:   return "Critically Low"
    if live_pct < 50:   return "Low"
    if live_pct < 75:   return "Normal"
    return "High"


def _float(val, default=0.0) -> float:
    """Safe float conversion, stripping non-numeric chars."""
    try:
        return float(re.sub(r"[^\d.\-]", "", str(val or "")) or default)
    except (ValueError, TypeError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1 — RSMS Laravel/Lumen JSON API
# (Approach A from session summary: discover the actual backend endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rsms_api() -> list:
    """
    Probe plausible Laravel/Lumen REST routes on the RSMS admin backend.
    The Angular frontend calls one of these internally — we try each until
    we get a valid JSON response containing reservoir data.

    Returns list of reservoir dicts, or [] if all routes return 404/error.
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    headers = {
        "Accept":       "application/json",
        "Content-Type": "application/json",
        "Referer":      f"{RSMS_BASE_URL}/",
        "Origin":       RSMS_BASE_URL,
    }

    for route in RSMS_API_CANDIDATES:
        url = f"{RSMS_API_BASE}{route}"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 404:
                continue        # route doesn't exist — try next
            if resp.status_code != 200:
                continue
            ct = resp.headers.get("Content-Type", "")
            if "json" not in ct.lower():
                continue        # HTML or redirect — Angular route, not API
            data = resp.json()

            # Extract records from common Laravel response shapes
            raw = (
                data if isinstance(data, list) else
                data.get("data") or
                data.get("result") or
                data.get("bulletins") or
                data.get("reservoirs") or
                []
            )
            if not raw or not isinstance(raw, list):
                continue

            # Try to parse as bulletin list (each item = one bulletin metadata)
            # or as direct reservoir storage rows
            records = _parse_rsms_api_rows(raw, today_str, url)
            if records:
                print(f"   ✅ RSMS API found: {url}  ({len(records)} reservoirs)")
                return records

        except Exception as exc:
            print(f"   RSMS API {url}: {exc}")
            continue

    print("   RSMS API: no working endpoint found — falling through to PDF")
    return []


def _parse_rsms_api_rows(raw: list, today_str: str, source_url: str) -> list:
    """
    Parse JSON rows from RSMS API — handles both direct reservoir rows
    and bulletin-metadata rows (which need a further PDF fetch to get data).
    Returns [] if the data shape isn't recognisable as reservoir storage.
    """
    records = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        item_l = {k.lower(): v for k, v in item.items()}

        # Look for reservoir storage fields
        name = (
            item_l.get("reservoir_name") or item_l.get("reservoirname") or
            item_l.get("name") or item_l.get("dam_name") or ""
        ).strip()
        if not name:
            continue

        cap  = _float(item_l.get("total_capacity") or item_l.get("totalcapacity") or
                      CAPACITY_LOOKUP.get(name, {}).get("capacity_bcm", 0))
        live = _float(item_l.get("live_storage") or item_l.get("livestorage") or
                      item_l.get("current_storage") or 0)
        avg  = _float(item_l.get("normal_storage") or item_l.get("normalavgstorage") or
                      item_l.get("avg_storage") or 0)
        state = (
            item_l.get("state_name") or item_l.get("statename") or
            item_l.get("state") or
            CAPACITY_LOOKUP.get(name, {}).get("state", "")
        ).strip()

        live_pct = round(live / cap * 100, 1) if cap else 0.0
        avg_pct  = round(avg  / cap * 100, 1) if cap else 0.0

        records.append({
            "date":             today_str,
            "name":             name,
            "state":            state,
            "capacity_bcm":     round(cap,  2),
            "live_storage_bcm": round(live, 3),
            "live_storage_pct": live_pct,
            "ten_yr_avg_pct":   avg_pct,
            "deficit_pct":      round(live_pct - avg_pct, 1),
            "status":           storage_status(live_pct),
            "source":           f"RSMS API ({source_url})",
        })
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 — RSMS PDF Bulletin (direct URL construction from session summary)
# ─────────────────────────────────────────────────────────────────────────────

def _rsms_bulletin_candidates() -> list[tuple[date, int, str]]:
    """
    Generate (bulletin_date, seq_number, url) tuples for the last 4 Thursdays.
    Sequence is calculated from the session-summary anchor:
      bulletin #105 = Thursday 18 Jun 2026.
    """
    today = date.today()
    days_since_thu = (today.weekday() - 3) % 7   # Thursday = weekday 3
    last_thursday  = today - timedelta(days=days_since_thu)
    weeks_from_anchor = (last_thursday - RSMS_ANCHOR_DATE).days // 7
    current_seq = RSMS_ANCHOR_SEQ + weeks_from_anchor

    candidates = []
    for w in range(4):   # try current + 3 previous Thursdays
        d   = last_thursday - timedelta(weeks=w)
        seq = current_seq  - w
        if seq < 1:
            break
        url = (
            f"{RSMS_BULLETIN_BASE}/"
            f"bulletin-{d.day:02d}-{d.month:02d}-{d.year}-{seq}.pdf"
        )
        candidates.append((d, seq, url))
    return candidates


def fetch_rsms_pdf() -> list:
    """
    Download the latest RSMS bulletin PDF using the confirmed URL pattern
    from session_summary.txt and parse it with pdfplumber.

    URL pattern: bulletin-DD-MM-YYYY-SEQ.pdf
    Sequence anchor: #105 = 18 Jun 2026 (from session summary)
    """
    try:
        import pdfplumber
    except ImportError:
        print("   pdfplumber not installed — skipping RSMS PDF tier")
        return []

    candidates = _rsms_bulletin_candidates()
    print(f"   RSMS PDF: trying {len(candidates)} candidates …")

    for bulletin_date, seq, url in candidates:
        try:
            resp = requests.get(url, timeout=45)
            if resp.status_code != 200:
                print(f"   RSMS PDF [{seq}] {bulletin_date} → HTTP {resp.status_code}")
                continue

            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            records   = _parse_rsms_pdf(resp.content, today_str, url)

            if records:
                print(
                    f"   ✅ RSMS PDF: bulletin-{bulletin_date.day:02d}-"
                    f"{bulletin_date.month:02d}-{bulletin_date.year}-{seq}.pdf"
                    f"  ({len(records)} reservoirs)"
                )
                return records
            else:
                print(f"   RSMS PDF [{seq}] parsed 0 rows — trying previous week")

        except Exception as exc:
            print(f"   RSMS PDF [{seq}] {url}: {exc}")

    return []


def _parse_rsms_pdf(content: bytes, today_str: str, source_url: str) -> list:
    """
    Extract reservoir storage table from an RSMS bulletin PDF.

    RSMS bulletin column pattern (typical):
      Sl.No | Region | State | Reservoir Name | Total Capacity |
      Live Storage (Current) | % to Capacity | Last Year Live Storage | Normal Storage

    pdfplumber is used for table extraction; falls back to text parsing
    if table extraction returns nothing useful.
    """
    import pdfplumber

    records = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            # ── Attempt 1: structured table extraction ──
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    parsed = _parse_rsms_row(row, today_str, source_url)
                    if parsed:
                        records.append(parsed)

            # ── Attempt 2: text-based fallback if table extraction found nothing ──
            if not records and page_num == 1:
                text = page.extract_text() or ""
                records.extend(_parse_rsms_text(text, today_str, source_url))

    # Deduplicate by reservoir name (keep last occurrence — usually most complete)
    seen = {}
    for r in records:
        seen[r["name"].lower()] = r
    return list(seen.values())


def _parse_rsms_row(row: list, today_str: str, source_url: str) -> dict | None:
    """
    Parse one table row from an RSMS PDF.
    RSMS columns (0-indexed, typical layout):
      0: Sl.No   1: Region   2: State   3: Reservoir Name
      4: Total Capacity (BCM)   5: Live Storage Current (BCM)
      6: % to Total Capacity   7: Last Year Storage   8: Normal Storage
    Also handles compact 7-column layout without Region column.
    """
    if not row or len(row) < 5:
        return None

    # Detect column layout by checking if col[1] looks like a region name
    # RSMS uses 4 regions: Northern, Eastern, Western, Southern, Central
    REGIONS = {"northern", "eastern", "western", "southern", "central"}
    has_region = str(row[1] or "").strip().lower() in REGIONS

    if has_region:
        # 9-column layout: Sl | Region | State | Name | Cap | Live | % | LY | Normal
        state_col = 2; name_col = 3; cap_col = 4; live_col = 5; pct_col = 6; avg_col = 8
    else:
        # 7-column layout: Sl | State | Name | Cap | Live | % | Normal
        state_col = 1; name_col = 2; cap_col = 3; live_col = 4; pct_col = 5; avg_col = 6

    def _get(idx):
        return row[idx] if len(row) > idx else ""

    name  = str(_get(name_col)  or "").strip()
    state = str(_get(state_col) or "").strip()

    if not name:
        return None
    # Skip header rows
    if re.match(r"(?i)^(sl\.?|no\.?|reservoir|name|dam|total|live|#)", name):
        return None
    # Skip region/summary rows (ALL CAPS short strings)
    if len(name) <= 3 or name.upper() == name and len(name) < 12:
        return None

    cap      = _float(_get(cap_col))
    live     = _float(_get(live_col))
    live_pct = _float(_get(pct_col))
    avg      = _float(_get(avg_col))

    # Compute % if missing but capacity and live are present
    if live_pct == 0 and cap > 0 and live > 0:
        live_pct = round(live / cap * 100, 1)

    avg_pct = round(avg / cap * 100, 1) if (avg > 0 and cap > 0) else 0.0

    # Supplement missing state from lookup
    if not state:
        state = CAPACITY_LOOKUP.get(name, {}).get("state", "")

    return {
        "date":             today_str,
        "name":             name,
        "state":            state,
        "capacity_bcm":     round(cap,  3),
        "live_storage_bcm": round(live, 3),
        "live_storage_pct": round(live_pct, 1),
        "ten_yr_avg_pct":   avg_pct,
        "deficit_pct":      round(live_pct - avg_pct, 1),
        "status":           storage_status(live_pct),
        "source":           f"RSMS Bulletin PDF ({source_url})",
    }


def _parse_rsms_text(text: str, today_str: str, source_url: str) -> list:
    """
    Text-based fallback parser for RSMS PDF pages where table extraction fails
    (e.g. scanned or image-heavy pages). Looks for lines with BCM values.
    """
    records = []
    # Pattern: Name followed by numeric values (capacity, live, %)
    pattern = re.compile(
        r"([A-Za-z][A-Za-z\s\(\)\.]{4,40})\s+"   # reservoir name
        r"(\d+\.\d+)\s+"                           # capacity BCM
        r"(\d+\.\d+)\s+"                           # live storage BCM
        r"(\d+(?:\.\d+)?)"                         # % to capacity
    )
    for m in pattern.finditer(text):
        name     = m.group(1).strip()
        cap      = _float(m.group(2))
        live     = _float(m.group(3))
        live_pct = _float(m.group(4))
        avg_pct  = 0.0

        if cap == 0 or len(name) < 5:
            continue

        records.append({
            "date":             today_str,
            "name":             name,
            "state":            CAPACITY_LOOKUP.get(name, {}).get("state", ""),
            "capacity_bcm":     round(cap,  3),
            "live_storage_bcm": round(live, 3),
            "live_storage_pct": round(live_pct, 1),
            "ten_yr_avg_pct":   avg_pct,
            "deficit_pct":      round(live_pct - avg_pct, 1),
            "status":           storage_status(live_pct),
            "source":           f"RSMS Bulletin PDF text-fallback ({source_url})",
        })
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3 — India-WRIS JSON API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_wris() -> list:
    today     = datetime.now(timezone.utc)
    today_str = today.strftime("%Y-%m-%d")
    try:
        resp = requests.post(
            "https://indiawris.gov.in/api/ReservoirStorage/getReservoirStorageDetails",
            json={"date": today.strftime("%d/%m/%Y")},
            headers={
                "Content-Type": "application/json",
                "Accept":       "application/json",
                "Origin":       "https://indiawris.gov.in",
                "Referer":      "https://indiawris.gov.in/wris/",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        raw  = data if isinstance(data, list) else data.get("data", data.get("result", []))
        if not raw:
            return []

        records = []
        for item in raw:
            il   = {k.lower(): v for k, v in item.items()}
            name = (il.get("reservoirname") or il.get("reservoir_name") or il.get("name") or "").strip()
            if not name:
                continue
            cap  = _float(il.get("totalcapacity") or il.get("total_capacity") or
                          CAPACITY_LOOKUP.get(name, {}).get("capacity_bcm", 0))
            live = _float(il.get("livestorage")   or il.get("live_storage") or il.get("currentstorage"))
            avg  = _float(il.get("normalavgstorage") or il.get("avg_storage") or il.get("lastyearstorage"))
            state = (il.get("statename") or il.get("state_name") or il.get("state") or
                     CAPACITY_LOOKUP.get(name, {}).get("state", "")).strip()
            live_pct = round(live / cap * 100, 1) if cap else 0.0
            avg_pct  = round(avg  / cap * 100, 1) if cap else 0.0
            records.append({
                "date":             today_str,
                "name":             name,
                "state":            state,
                "capacity_bcm":     round(cap,  2),
                "live_storage_bcm": round(live, 3),
                "live_storage_pct": live_pct,
                "ten_yr_avg_pct":   avg_pct,
                "deficit_pct":      round(live_pct - avg_pct, 1),
                "status":           storage_status(live_pct),
                "source":           "India-WRIS (CWC/NHP)",
            })
        return records
    except Exception as exc:
        print(f"   India-WRIS failed: {exc}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Tier 4 — data.gov.in OGD Platform
# ─────────────────────────────────────────────────────────────────────────────

OGD_URL = (
    "https://api.data.gov.in/resource/c9f59f3c-3c0e-4954-8c1e-c5e5e3e0e8b5"
    "?api-key=579b464db66ec23bdd000001cdd3946e6ce24232511d86821994f333"
    "&format=json&limit=50"
)

def fetch_ogd() -> list:
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        resp = requests.get(OGD_URL, timeout=30)
        resp.raise_for_status()
        records = []
        for item in resp.json().get("records", []):
            name  = str(item.get("reservoir_name", "")).strip()
            state = str(item.get("state", "")).strip()
            if not name:
                continue
            cap      = _float(item.get("total_capacity_bcm"))
            live     = _float(item.get("live_storage_bcm"))
            live_pct = round(live / cap * 100, 1) if cap else 0.0
            avg_pct  = _float(item.get("avg_storage_pct"))
            records.append({
                "date":             today_str,
                "name":             name,
                "state":            state,
                "capacity_bcm":     round(cap,  2),
                "live_storage_bcm": round(live, 3),
                "live_storage_pct": live_pct,
                "ten_yr_avg_pct":   avg_pct,
                "deficit_pct":      round(live_pct - avg_pct, 1),
                "status":           storage_status(live_pct),
                "source":           "data.gov.in OGD (CWC)",
            })
        return records
    except Exception as exc:
        print(f"   data.gov.in OGD failed: {exc}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Fetching reservoir storage (RSMS API → RSMS PDF → WRIS → OGD) …")

    tiers = [
        ("RSMS API",       fetch_rsms_api),
        ("RSMS PDF",       fetch_rsms_pdf),
        ("India-WRIS",     fetch_wris),
        ("data.gov.in OGD", fetch_ogd),
    ]

    records = []
    for label, fn in tiers:
        records = fn()
        if records:
            print(f"   ✅ {label}: {len(records)} reservoirs")
            break
        print(f"   {label} returned no data — trying next tier …")

    if not records:
        raise RuntimeError(
            "All reservoir tiers failed:\n"
            "  Tier 1: RSMS API (rsms.cwc.gov.in/admin/*)\n"
            "  Tier 2: RSMS PDF (rsms.cwc.gov.in/admin/storage/bulletins/bulletin-DD-MM-YYYY-SEQ.pdf)\n"
            "  Tier 3: India-WRIS (indiawris.gov.in)\n"
            "  Tier 4: data.gov.in OGD\n"
            "Check network access and verify rsms.cwc.gov.in is reachable."
        )

    out_path = DATA_DIR / "reservoirs.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)

    n_critical = sum(1 for r in records if r["live_storage_pct"] < 25)
    n_low      = sum(1 for r in records if 25 <= r["live_storage_pct"] < 50)
    print(f"✅  Saved {len(records)} reservoirs → {out_path}")
    print(f"   Critical (<25%): {n_critical}  Low (25–50%): {n_low}  of {len(records)}")


if __name__ == "__main__":
    main()
