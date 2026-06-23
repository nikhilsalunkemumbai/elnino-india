"""
update_reservoirs.py
Fetches India major reservoir storage from the CWC RSMS weekly bulletin PDF.

Source
------
  CWC Reservoir Storage Monitoring System (RSMS)
  https://rsms.cwc.gov.in
  Published every Thursday.

  Bulletin URL pattern (confirmed from bulletin #105, 18 Jun 2026):
    https://rsms.cwc.gov.in/admin/storage/bulletins/bulletin-DD-MM-YYYY-SEQ.pdf

  Sequence number: anchored at #105 for 18 Jun 2026; increments by 1 each Thursday.

Authentication
--------------
  The bulletin PDF endpoint requires an authenticated session from CI runners
  (returns HTTP 401 without a valid cookie). To enable live data:

    1. Log in at https://rsms.cwc.gov.in in a browser
    2. Open DevTools → Application → Cookies → copy the session cookie string
    3. Add as a GitHub Actions secret named RSMS_SESSION_COOKIE

  Without the cookie the script falls back to stale git data and exits 0
  (reservoir failure must not block ENSO/IOD/rainfall deployment).

Data extracted
--------------
  Regional summary rows from the bulletin — NOT individual reservoir rows.
  The bulletin provides pre-computed regional totals (Northern / Western /
  Central / Eastern / Southern / All India) which are all the dashboard needs.
  Parsing summary rows is simpler and more robust than parsing 166 reservoir rows.

Pipeline
--------
  Tier 1 — RSMS bulletin PDF  (requires RSMS_SESSION_COOKIE secret)
  Tier 2 — Stale git data     (last committed reservoirs.csv; never fails)

Output: data/reservoirs.csv
  Columns: date, name, state, capacity_bcm, live_storage_bcm,
           live_storage_pct, ten_yr_avg_pct, deficit_pct, status, source
"""

import csv
import io
import os
import re
import requests
import subprocess
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── RSMS constants ────────────────────────────────────────────────────────────
RSMS_BULLETIN_BASE = "https://rsms.cwc.gov.in/admin/storage/bulletins"
RSMS_ANCHOR_DATE   = date(2026, 6, 18)   # confirmed Thursday, bulletin #105
RSMS_ANCHOR_SEQ    = 105
# Note: sequence number is believed to increment weekly from the anchor above.
# If RSMS resets their sequence counter, update RSMS_ANCHOR_DATE and
# RSMS_ANCHOR_SEQ to a freshly confirmed bulletin filename.

# ── Capacity reference (used to fill state when parsing summary rows) ─────────
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
        "Accept":  "application/pdf, */*",
        "Referer": "https://rsms.cwc.gov.in/",
    }
    if cookie:
        h["Cookie"] = cookie
    return h


# ─────────────────────────────────────────────────────────────────────────────
# Bulletin URL candidates
# ─────────────────────────────────────────────────────────────────────────────

def rsms_candidates() -> list[tuple[date, int, str]]:
    """
    Returns (date, seq, url) for the last 4 Thursdays.
    Sequence is derived from the confirmed anchor: #105 = 18 Jun 2026.
    Try 4 candidates so a missed week doesn't block the pipeline.
    """
    today          = date.today()
    days_since_thu = (today.weekday() - 3) % 7
    last_thursday  = today - timedelta(days=days_since_thu)
    weeks_since    = (last_thursday - RSMS_ANCHOR_DATE).days // 7
    current_seq    = RSMS_ANCHOR_SEQ + weeks_since
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
# Tier 1 — RSMS bulletin PDF
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rsms_pdf() -> list:
    """
    Downloads the RSMS weekly bulletin PDF and extracts regional summary rows.
    Requires RSMS_SESSION_COOKIE secret (see module docstring).
    Returns [] if the PDF cannot be fetched or parsed.
    """
    try:
        import pdfplumber
    except ImportError:
        print("   pdfplumber not installed — skipping RSMS PDF tier")
        return []

    cookie     = os.environ.get("RSMS_SESSION_COOKIE", "").strip()
    today_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    candidates = rsms_candidates()

    if not cookie:
        print(
            "   ℹ️  RSMS_SESSION_COOKIE not set — bulletin PDF will likely return 401.\n"
            "   To enable live reservoir data: repo Settings → Secrets → RSMS_SESSION_COOKIE"
        )

    print(f"   RSMS PDF: trying {len(candidates)} candidates …")
    for bulletin_date, seq, url in candidates:
        try:
            resp = requests.get(url, headers=_headers(cookie), timeout=45)
            if resp.status_code == 401:
                print(f"   RSMS PDF [{seq}]: 401 Unauthorized — cookie missing or expired")
                break   # All candidates will return 401 without a valid cookie
            if resp.status_code != 200:
                print(f"   RSMS PDF [{seq}]: HTTP {resp.status_code} — skipping")
                continue
            records = _parse_bulletin_pdf(resp.content, today_str, url)
            if records:
                print(
                    f"   ✅ RSMS PDF [{seq}] "
                    f"bulletin-{bulletin_date.day:02d}-{bulletin_date.month:02d}"
                    f"-{bulletin_date.year}-{seq}.pdf  ({len(records)} rows)"
                )
                return records
            print(f"   RSMS PDF [{seq}]: downloaded but 0 rows parsed")
        except Exception as exc:
            print(f"   RSMS PDF [{seq}]: {exc}")

    return []


def _parse_bulletin_pdf(content: bytes, today_str: str, source_url: str) -> list:
    """
    Extracts regional summary rows from the bulletin PDF.
    The bulletin provides pre-computed rows for Northern, Western, Central,
    Eastern, Southern regions and an All India total — 6 rows per bulletin.
    These are the only rows the dashboard requires.
    """
    import pdfplumber

    REGION_NAMES = {"northern", "western", "central", "eastern", "southern", "all india"}
    records = []

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                for row in table:
                    if not row:
                        continue
                    # Find rows where first or second cell matches a region name
                    cells = [str(c or "").strip() for c in row]
                    label = next(
                        (c for c in cells[:3] if c.lower() in REGION_NAMES),
                        None
                    )
                    if not label:
                        continue
                    nums = [_float(c) for c in cells if _float(c) > 0]
                    if len(nums) < 2:
                        continue
                    # Column order in RSMS summary table:
                    # Total capacity | Last year storage | Last year % | Normal storage | Normal % | Current storage | Current %
                    # Positions vary slightly — take the last 3 positive numerics as
                    # (normal_pct, current_storage, current_pct) when 3+ are present
                    capacity     = nums[0] if nums else 0.0
                    current_pct  = nums[-1] if len(nums) >= 1 else 0.0
                    last_yr_pct  = nums[-3] if len(nums) >= 3 else 0.0
                    normal_pct   = nums[-5] if len(nums) >= 5 else 0.0
                    live_bcm     = nums[-2] if len(nums) >= 2 else 0.0

                    records.append({
                        "date":             today_str,
                        "name":             label.title(),
                        "state":            label.title(),
                        "capacity_bcm":     round(capacity,    2),
                        "live_storage_bcm": round(live_bcm,    3),
                        "live_storage_pct": round(current_pct, 1),
                        "ten_yr_avg_pct":   round(normal_pct,  1),
                        "deficit_pct":      round(current_pct - normal_pct, 1),
                        "status":           storage_status(current_pct),
                        "source":           f"CWC RSMS Bulletin PDF ({source_url})",
                    })

    # Deduplicate by region name (keep first occurrence)
    seen    = {}
    for r in records:
        seen.setdefault(r["name"].lower(), r)
    return list(seen.values())


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 — Stale git data (last committed reservoirs.csv)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_stale_from_git() -> list:
    """
    Re-uses the last committed reservoirs.csv. Marks rows as STALE.
    Ensures the dashboard never goes blank. Never raises.
    """
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
                # Avoid compounding STALE labels on repeated failures
                src = row.get("source", "unknown")
                if "STALE" not in src:
                    src = f"STALE (last known) — {src}"
                row["source"] = src
                records.append(row)
            if records:
                print(f"   ✅ Stale fallback: {len(records)} rows from last git commit")
                return records
    except Exception as exc:
        print(f"   Stale git fallback failed: {exc}")

    # First-ever run or git history unavailable — write minimal stub
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
    print("Fetching reservoir storage (RSMS PDF → stale fallback) …")

    records     = fetch_rsms_pdf()
    source_used = "RSMS PDF"

    if not records:
        print("   RSMS PDF returned no data — falling back to stale git data …")
        records     = fetch_stale_from_git()
        source_used = "Stale git data"

    out_path   = DATA_DIR / "reservoirs.csv"
    fieldnames = list(records[0].keys()) if records else [
        "date", "name", "state", "capacity_bcm", "live_storage_bcm",
        "live_storage_pct", "ten_yr_avg_pct", "deficit_pct", "status", "source"
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    n_stale = sum(1 for r in records if "STALE" in str(r.get("source", "")))
    print(f"✅  Saved {len(records)} rows → {out_path}  [source: {source_used}]")
    if n_stale:
        print(f"   ⚠️  {n_stale} rows are stale — RSMS PDF unreachable (cookie missing or expired)")
    else:
        n_low = sum(1 for r in records if _float(r.get("live_storage_pct", 0)) < 50)
        print(f"   {n_low}/{len(records)} regions below 50% live storage")


if __name__ == "__main__":
    main()
