"""
update_reservoirs.py
Fetches India major reservoir live storage from CWC RSMS.

═══════════════════════════════════════════════════════════════════════════════
SESSION SUMMARY ALIGNMENT (session_summary.txt)
═══════════════════════════════════════════════════════════════════════════════

Key facts established:
  • RSMS = Reservoir Storage Monitoring System (rsms.cwc.gov.in)
  • RSMS is the authoritative CWC reservoir data source
  • Published every Thursday
  • Bulletin URL pattern (confirmed):
      https://rsms.cwc.gov.in/admin/storage/bulletins/bulletin-DD-MM-YYYY-SEQ.pdf
  • Anchor: bulletin-18-06-2026-105.pdf = Thursday 18 Jun 2026
  • Backend: Laravel/Lumen 11.1.0 at rsms.cwc.gov.in/admin/
  • Frontend: Angular — does NOT render bulletin links in static HTML
  • Angular calls a backend API to populate bulletin listings

Recommended order per session summary:
  Approach A: Discover and call the Laravel/Lumen API directly (most robust)
  Approach B: Scan Angular JS bundles for API endpoint references
  Approach C: Direct PDF URL construction from known pattern + sequence

RSMS PDF auth status:
  • /admin/storage/bulletins/ returned HTTP 401 from GitHub Actions runner
  • Session summary described PDFs as "publicly accessible" — but this was
    observed from an authenticated browser session (cookie present)
  • RSMS_SESSION_COOKIE secret enables Tier 1B and all PDF download attempts
  • Without the cookie, Tier 1A (RSMS API probe — may be public) is tried first

User observation: "rsms html shows bulletin from last thursday"
  • The Angular frontend at rsms.cwc.gov.in IS accessible and displays the
    latest bulletin. The Angular app fetches bulletin data from a backend API.
  • We probe that API without auth first — public listing endpoints are common
    in Laravel even when file downloads require auth.

═══════════════════════════════════════════════════════════════════════════════
TIER ORDER (RSMS-first as per session summary)
═══════════════════════════════════════════════════════════════════════════════

Tier 1A — RSMS Laravel/Lumen API (no auth — public listing probe)
  Probes plausible REST routes at rsms.cwc.gov.in/admin/api/* without cookie.
  A bulletin listing API would return JSON with bulletin metadata + PDF URLs.
  If found, also attempts to download the referenced PDF (with cookie if set).

Tier 1B — RSMS PDF direct URL (Approach C from session summary)
  Constructs the PDF URL from anchor date + sequence number.
  Tries WITHOUT cookie first (per "publicly accessible" session summary note).
  If 401, retries WITH cookie if RSMS_SESSION_COOKIE secret is configured.
  Parses with pdfplumber.

Tier 1C — RSMS Angular bundle scan (Approach B from session summary)
  Downloads rsms.cwc.gov.in JS bundles and searches for API endpoint strings.
  If found, calls the discovered endpoint.

Tier 2 — CWC general HTML (cwc.gov.in)
  Public HTML page. Older data (as noted by user) but no auth required.

Tier 3 — Stale git data (non-fatal)
  Re-uses last committed reservoirs.csv. Marks rows as STALE.
  Ensures dashboard never goes blank.

NON-FATAL: script exits 0 regardless — reservoir failure must not block
ENSO/IOD/SOI/rainfall deployment.

Output: data/reservoirs.csv
"""

import csv
import io
import json
import os
import re
import requests
import subprocess
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── RSMS constants (session_summary.txt) ──────────────────────────────────────
RSMS_BASE_URL      = "https://rsms.cwc.gov.in"
RSMS_BULLETIN_BASE = f"{RSMS_BASE_URL}/admin/storage/bulletins"
RSMS_API_BASE      = f"{RSMS_BASE_URL}/admin"
RSMS_ANCHOR_DATE   = date(2026, 6, 18)   # confirmed Thursday, bulletin #105
RSMS_ANCHOR_SEQ    = 105

# Plausible Laravel/Lumen REST routes (no auth probe — public listing)
RSMS_API_ROUTES = [
    "/api/bulletins",
    "/api/bulletin",
    "/api/bulletin-list",
    "/api/bulletins/latest",
    "/api/bulletin/latest",
    "/api/reservoirs",
    "/api/reservoir-storage",
    "/api/reservoir-storage/latest",
    "/api/storage",
    "/api/v1/bulletins",
    "/api/v1/bulletin",
    "/api/v1/reservoir-storage",
    "/bulletins",
    "/reservoir-storage",
    "/storage/latest",
]

# ── CWC general HTML (fallback) ───────────────────────────────────────────────
CWC_HTML_URL = "https://cwc.gov.in/reservoir-storage-information"

# ── Capacity reference ────────────────────────────────────────────────────────
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


def storage_status(pct: float) -> str:
    if pct < 25:  return "Critically Low"
    if pct < 50:  return "Low"
    if pct < 75:  return "Normal"
    return "High"


def _float(val, default=0.0) -> float:
    try:
        return float(re.sub(r"[^\d.\-]", "", str(val or "")) or default)
    except (ValueError, TypeError):
        return default


def _headers(cookie: str = "") -> dict:
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept":  "application/json, text/html, */*",
        "Referer": f"{RSMS_BASE_URL}/",
        "Origin":  RSMS_BASE_URL,
    }
    if cookie:
        h["Cookie"] = cookie
    return h


# ─────────────────────────────────────────────────────────────────────────────
# RSMS bulletin candidate URL generator
# ─────────────────────────────────────────────────────────────────────────────

def rsms_candidates() -> list[tuple[date, int, str]]:
    """
    Yields (date, seq, url) for the last 4 Thursdays.
    Sequence derived from session summary anchor: #105 = 18 Jun 2026.
    """
    today          = date.today()
    days_since_thu = (today.weekday() - 3) % 7
    last_thursday  = today - timedelta(days=days_since_thu)
    weeks          = (last_thursday - RSMS_ANCHOR_DATE).days // 7
    current_seq    = RSMS_ANCHOR_SEQ + weeks
    result         = []
    for w in range(4):
        d   = last_thursday - timedelta(weeks=w)
        seq = current_seq - w
        if seq < 1:
            break
        result.append((
            d, seq,
            f"{RSMS_BULLETIN_BASE}/bulletin-{d.day:02d}-{d.month:02d}-{d.year}-{seq}.pdf"
        ))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1A — RSMS Laravel/Lumen API (no-auth probe first, then with cookie)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rsms_api() -> list:
    """
    Probe RSMS Lumen REST routes for a public bulletin/reservoir listing.
    Tries without cookie first (public listing APIs are common in Laravel).
    Falls back to cookie-authenticated request if RSMS_SESSION_COOKIE is set.
    """
    cookie    = os.environ.get("RSMS_SESSION_COOKIE", "").strip()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Try without auth first, then with auth
    cookie_options = [""] + ([cookie] if cookie else [])

    for ck in cookie_options:
        auth_label = "with cookie" if ck else "no auth"
        for route in RSMS_API_ROUTES:
            url = f"{RSMS_API_BASE}{route}"
            try:
                resp = requests.get(
                    url, headers=_headers(ck), timeout=15
                )
                if resp.status_code in (401, 403, 404):
                    continue
                if resp.status_code != 200:
                    continue
                if "json" not in resp.headers.get("Content-Type", "").lower():
                    continue
                data = resp.json()
                # Skip Laravel "route not found" responses
                if isinstance(data, dict) and data.get("message") == "Route not found.":
                    continue
                raw = (
                    data if isinstance(data, list) else
                    data.get("data") or data.get("result") or
                    data.get("bulletins") or data.get("reservoirs") or []
                )
                if not raw or not isinstance(raw, list):
                    continue
                records = _parse_api_rows(raw, today_str, url)
                if records:
                    print(f"   ✅ RSMS API ({auth_label}): {url}  ({len(records)} reservoirs)")
                    return records
            except Exception as exc:
                print(f"   RSMS API {route} ({auth_label}): {exc}")

    print("   RSMS API: no working endpoint found")
    return []


def _parse_api_rows(raw: list, today_str: str, source_url: str) -> list:
    records = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        il    = {k.lower(): v for k, v in item.items()}
        name  = (il.get("reservoir_name") or il.get("reservoirname") or
                 il.get("name") or il.get("dam_name") or "").strip()
        if not name:
            continue
        cap   = _float(il.get("total_capacity") or il.get("totalcapacity") or
                       CAPACITY_LOOKUP.get(name, {}).get("capacity_bcm", 0))
        live  = _float(il.get("live_storage") or il.get("livestorage") or
                       il.get("current_storage") or il.get("currentstorage"))
        avg   = _float(il.get("normal_storage") or il.get("normalavgstorage") or
                       il.get("avg_storage") or il.get("ten_year_avg"))
        state = (il.get("state_name") or il.get("statename") or il.get("state") or
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
            "source":           f"RSMS API ({source_url})",
        })
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1B — RSMS PDF direct URL (Approach C from session summary)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rsms_pdf() -> list:
    """
    Constructs the RSMS bulletin PDF URL from the confirmed pattern and
    anchor date in session_summary.txt.

    Tries WITHOUT cookie first (session summary says "publicly accessible").
    If 401, retries WITH cookie (RSMS_SESSION_COOKIE secret).

    To enable cookie: GitHub repo → Settings → Secrets and variables →
    Actions → New repository secret → Name: RSMS_SESSION_COOKIE
    Value: the session cookie string from browser DevTools after logging
    in at https://rsms.cwc.gov.in/
    """
    try:
        import pdfplumber
    except ImportError:
        print("   pdfplumber not installed — skipping RSMS PDF tier")
        return []

    cookie     = os.environ.get("RSMS_SESSION_COOKIE", "").strip()
    today_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    candidates = rsms_candidates()
    print(f"   RSMS PDF: trying {len(candidates)} candidates …")

    for bulletin_date, seq, url in candidates:
        # Attempt 1: no cookie (session summary: "publicly accessible")
        for ck, auth_label in [("", "no auth"), (cookie, "with cookie")]:
            if auth_label == "with cookie" and not cookie:
                continue   # no point retrying without a cookie to use
            try:
                resp = requests.get(url, headers=_headers(ck), timeout=45)

                if resp.status_code == 200:
                    records = _parse_rsms_pdf(resp.content, today_str, url)
                    if records:
                        print(
                            f"   ✅ RSMS PDF [{seq}] "
                            f"bulletin-{bulletin_date.day:02d}-{bulletin_date.month:02d}"
                            f"-{bulletin_date.year}-{seq}.pdf  "
                            f"({len(records)} reservoirs, {auth_label})"
                        )
                        return records
                    print(f"   RSMS PDF [{seq}] ({auth_label}): downloaded but 0 rows parsed")
                    break   # don't retry with cookie if we got 200 but no data

                elif resp.status_code == 401:
                    print(f"   RSMS PDF [{seq}] ({auth_label}): 401 Unauthorized")
                    if not cookie:
                        print(
                            "   → Add RSMS_SESSION_COOKIE as a GitHub Actions secret to enable auth"
                        )
                    # continue to next auth option (try with cookie)

                else:
                    print(f"   RSMS PDF [{seq}] ({auth_label}): HTTP {resp.status_code}")
                    break   # non-auth error — skip to next week

            except Exception as exc:
                print(f"   RSMS PDF [{seq}] ({auth_label}): {exc}")
                break

    return []


def _parse_rsms_pdf(content: bytes, today_str: str, source_url: str) -> list:
    import pdfplumber
    records = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                for row in table:
                    parsed = _parse_rsms_row(row, today_str, source_url)
                    if parsed:
                        records.append(parsed)
            if not records:
                text = page.extract_text() or ""
                records.extend(_parse_rsms_text(text, today_str, source_url))
    # Deduplicate by name
    seen = {}
    for r in records:
        seen[r["name"].lower()] = r
    return list(seen.values())


def _parse_rsms_row(row: list, today_str: str, source_url: str) -> dict | None:
    if not row or len(row) < 5:
        return None
    REGIONS    = {"northern", "eastern", "western", "southern", "central"}
    has_region = str(row[1] or "").strip().lower() in REGIONS
    if has_region:
        state_col, name_col, cap_col, live_col, pct_col, avg_col = 2, 3, 4, 5, 6, 8
    else:
        state_col, name_col, cap_col, live_col, pct_col, avg_col = 1, 2, 3, 4, 5, 6

    def _g(i): return row[i] if len(row) > i else ""

    name  = str(_g(name_col)  or "").strip()
    state = str(_g(state_col) or "").strip()
    if not name:
        return None
    if re.match(r"(?i)^(sl\.?|no\.?|reservoir|name|dam|total|live|#)", name):
        return None
    if len(name) <= 3 or (name.upper() == name and len(name) < 12):
        return None

    cap      = _float(_g(cap_col))
    live     = _float(_g(live_col))
    live_pct = _float(_g(pct_col))
    avg      = _float(_g(avg_col))
    if live_pct == 0 and cap > 0 and live > 0:
        live_pct = round(live / cap * 100, 1)
    avg_pct = round(avg / cap * 100, 1) if (avg > 0 and cap > 0) else 0.0
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
    records = []
    pattern = re.compile(
        r"([A-Za-z][A-Za-z\s\(\)\.]{4,40})\s+"
        r"(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+(?:\.\d+)?)"
    )
    for m in pattern.finditer(text):
        name, cap, live, live_pct = (
            m.group(1).strip(), _float(m.group(2)),
            _float(m.group(3)),  _float(m.group(4))
        )
        if cap == 0 or len(name) < 5:
            continue
        records.append({
            "date":             today_str,
            "name":             name,
            "state":            CAPACITY_LOOKUP.get(name, {}).get("state", ""),
            "capacity_bcm":     round(cap,  3),
            "live_storage_bcm": round(live, 3),
            "live_storage_pct": round(live_pct, 1),
            "ten_yr_avg_pct":   0.0,
            "deficit_pct":      0.0,
            "status":           storage_status(live_pct),
            "source":           f"RSMS PDF text-fallback ({source_url})",
        })
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1C — RSMS Angular bundle scan (Approach B from session summary)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rsms_bundle_scan() -> list:
    """
    Downloads the RSMS Angular app's main JS bundle and searches for
    API endpoint strings. If found, calls the endpoint directly.
    This implements Approach B from the session summary.
    """
    cookie    = os.environ.get("RSMS_SESSION_COOKIE", "").strip()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        # Fetch the RSMS homepage to find bundle script tags
        resp = requests.get(
            f"{RSMS_BASE_URL}/",
            headers=_headers(cookie), timeout=20
        )
        if resp.status_code != 200:
            print(f"   RSMS bundle scan: homepage returned {resp.status_code}")
            return []

        # Find Angular bundle JS filenames from <script src="..."> tags
        bundle_urls = re.findall(
            r'src=["\']([^"\']*(?:main|chunk|runtime)[^"\']*\.js)["\']',
            resp.text, re.IGNORECASE
        )
        if not bundle_urls:
            print("   RSMS bundle scan: no JS bundles found in HTML")
            return []

        # Download and search each bundle for API endpoint strings
        api_endpoints = set()
        for bundle_path in bundle_urls[:5]:   # limit to 5 bundles
            bundle_url = (
                bundle_path if bundle_path.startswith("http")
                else f"{RSMS_BASE_URL}/{bundle_path.lstrip('/')}"
            )
            try:
                br = requests.get(bundle_url, headers=_headers(cookie), timeout=20)
                if br.status_code != 200:
                    continue
                # Search for API path strings in minified JS
                # Patterns: "/api/...", "admin/api/...", etc.
                for match in re.finditer(
                    r'["\`]/(api/[a-zA-Z0-9/_\-]+)["\`]', br.text
                ):
                    endpoint = "/" + match.group(1)
                    if any(kw in endpoint.lower() for kw in
                           ["bulletin", "reservoir", "storage", "cwc"]):
                        api_endpoints.add(endpoint)
            except Exception as exc:
                print(f"   Bundle {bundle_url}: {exc}")

        if not api_endpoints:
            print("   RSMS bundle scan: no API endpoints found in bundles")
            return []

        print(f"   RSMS bundle scan: found {len(api_endpoints)} candidate endpoints: {api_endpoints}")

        # Call each discovered endpoint
        for endpoint in api_endpoints:
            url = f"{RSMS_API_BASE}{endpoint}"
            try:
                er = requests.get(url, headers=_headers(cookie), timeout=15)
                if er.status_code != 200:
                    continue
                if "json" not in er.headers.get("Content-Type", "").lower():
                    continue
                data = er.json()
                raw  = (
                    data if isinstance(data, list) else
                    data.get("data") or data.get("result") or
                    data.get("bulletins") or data.get("reservoirs") or []
                )
                if not raw:
                    continue
                records = _parse_api_rows(raw, today_str, url)
                if records:
                    print(f"   ✅ RSMS bundle-discovered API: {url}  ({len(records)} reservoirs)")
                    return records
            except Exception as exc:
                print(f"   Bundle endpoint {url}: {exc}")

    except Exception as exc:
        print(f"   RSMS bundle scan failed: {exc}")

    return []


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 — CWC general HTML (cwc.gov.in) — older data, no auth
# ─────────────────────────────────────────────────────────────────────────────

def fetch_cwc_html() -> list:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("   bs4 not installed — skipping CWC HTML tier")
        return []

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            CWC_HTML_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        resp.raise_for_status()
        soup    = BeautifulSoup(resp.text, "html.parser")
        records = []

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 3:
                continue
            header = [th.get_text(strip=True).lower()
                      for th in rows[0].find_all(["th", "td"])]
            if not any(k in " ".join(header)
                       for k in ["reservoir", "capacity", "storage"]):
                continue

            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["td","th"])]
                if len(cells) < 4:
                    continue
                name = state = ""
                for cell in cells:
                    text = cell.strip()
                    if (not name and len(text) > 4 and
                            not re.match(r"^[\d.\-]+$", text) and
                            not re.match(r"(?i)^(sl|no\.|s\.no|basin|region|total)", text)):
                        name = text
                    elif (name and not state and len(text) > 2 and
                            not re.match(r"^[\d.\-]+$", text)):
                        state = text

                nums = [_float(c) for c in cells if _float(c) > 0]
                if name and len(nums) >= 2:
                    cap      = nums[0] if nums[0] > 1 else 0.0
                    live     = nums[1] if len(nums) > 1 else 0.0
                    live_pct = nums[2] if len(nums) > 2 else (round(live/cap*100,1) if cap else 0.0)
                    avg_pct  = nums[3] if len(nums) > 3 else 0.0
                    if not state:
                        state = CAPACITY_LOOKUP.get(name, {}).get("state", "")
                    if cap == 0:
                        cap = CAPACITY_LOOKUP.get(name, {}).get("capacity_bcm", 0.0)
                    records.append({
                        "date":             today_str,
                        "name":             name,
                        "state":            state,
                        "capacity_bcm":     round(cap,  2),
                        "live_storage_bcm": round(live, 3),
                        "live_storage_pct": round(live_pct, 1),
                        "ten_yr_avg_pct":   round(avg_pct,  1),
                        "deficit_pct":      round(live_pct - avg_pct, 1),
                        "status":           storage_status(live_pct),
                        "source":           f"CWC HTML — note: may be older data ({CWC_HTML_URL})",
                    })
        return records

    except Exception as exc:
        print(f"   CWC HTML failed: {exc}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3 — Stale git data (non-fatal last resort)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_stale_from_git() -> list:
    out_path = DATA_DIR / "reservoirs.csv"
    try:
        result = subprocess.run(
            ["git", "show", "HEAD:data/reservoirs.csv"],
            capture_output=True, text=True,
            cwd=Path(__file__).parent.parent,
        )
        if result.returncode == 0 and result.stdout.strip():
            reader  = csv.DictReader(io.StringIO(result.stdout))
            records = []
            for row in reader:
                row["source"] = "STALE (last known) — " + row.get("source", "unknown")
                records.append(row)
            if records:
                print(f"   ✅ Stale fallback: {len(records)} reservoirs from last git commit")
                return records
    except Exception as exc:
        print(f"   Stale git fallback failed: {exc}")

    # First-ever run — write informative stub rows
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stubs = []
    for r in KNOWN_RESERVOIRS[:10]:
        stubs.append({
            "date":             today_str,
            "name":             r["name"],
            "state":            r["state"],
            "capacity_bcm":     r["capacity_bcm"],
            "live_storage_bcm": 0.0,
            "live_storage_pct": 0.0,
            "ten_yr_avg_pct":   0.0,
            "deficit_pct":      0.0,
            "status":           "Data Unavailable",
            "source":           "Stub — all live sources failed",
        })
    print(f"   No git history — writing {len(stubs)} stub rows")
    return stubs


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    cookie = os.environ.get("RSMS_SESSION_COOKIE", "").strip()
    print(
        "Fetching reservoir storage "
        "(RSMS API → RSMS PDF → RSMS bundle scan → CWC HTML → stale fallback) …"
    )
    if not cookie:
        print(
            "   ℹ️  RSMS_SESSION_COOKIE not set — RSMS PDF will be tried without auth first.\n"
            "   To enable cookie auth: repo Settings → Secrets → RSMS_SESSION_COOKIE"
        )

    tiers = [
        ("RSMS API",          fetch_rsms_api),         # Approach A — no auth then cookie
        ("RSMS PDF",          fetch_rsms_pdf),          # Approach C — no auth then cookie
        ("RSMS Bundle scan",  fetch_rsms_bundle_scan),  # Approach B — discover API from JS
        ("CWC HTML",          fetch_cwc_html),          # Fallback — older data, no auth
        ("Stale git data",    fetch_stale_from_git),    # Last resort — never fails
    ]

    records      = []
    source_used  = ""
    for label, fn in tiers:
        records = fn()
        if records:
            source_used = label
            break
        if label != "Stale git data":
            print(f"   {label} returned no data — trying next tier …")

    # Always write — dashboard must never go blank
    out_path   = DATA_DIR / "reservoirs.csv"
    fieldnames = list(records[0].keys()) if records else [
        "date","name","state","capacity_bcm","live_storage_bcm",
        "live_storage_pct","ten_yr_avg_pct","deficit_pct","status","source"
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    n_stale = sum(1 for r in records if "STALE" in str(r.get("source", "")))
    print(f"✅  Saved {len(records)} reservoirs → {out_path}  [source: {source_used}]")
    if n_stale:
        print(f"   ⚠️  {n_stale} rows are stale — no live RSMS source was reachable")
    else:
        n_low = sum(1 for r in records if _float(r.get("live_storage_pct", 0)) < 50)
        print(f"   {n_low}/{len(records)} below 50% live storage")


if __name__ == "__main__":
    main()
