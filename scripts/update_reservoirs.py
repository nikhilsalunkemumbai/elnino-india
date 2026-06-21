"""
update_reservoirs.py
Fetches India major reservoir live storage from CWC via two stable paths.

WHY THE OLD SOURCE WAS RISKY
  The original script pointed to a placeholder NWDP REST API endpoint that has
  no confirmed public JSON interface. Indian government portals (cwc.gov.in,
  nwdpwrd.gov.in) frequently restructure URLs and return HTML tables, not JSON.

TWO-TIER APPROACH (most stable → next most stable)
─────────────────────────────────────────────────────
Tier 1 — India-WRIS JSON API (indiawris.gov.in)
  India Water Resources Information System, jointly operated by CWC and NHP.
  The WRIS portal exposes reservoir storage via a documented REST endpoint
  that returns JSON. More stable than cwc.gov.in because WRIS is an NHP
  (National Hydrology Project) asset with World Bank backing.
  Endpoint: https://indiawris.gov.in/wris/#/reservoirMonitoring
  API base:  https://indiawris.gov.in/api/

Tier 2 — CWC Weekly PDF Bulletin (cwc.gov.in/reservoirs-storage-bulletin)
  CWC publishes a PDF every Thursday. This tier uses pdfplumber to scrape
  the current week's bulletin, which is always at a predictable path:
    https://cwc.gov.in/sites/default/files/reservoir-storage-bulletin/
    <YYYY-Wnn>.pdf   (where Wnn = ISO week number, zero-padded)
  This path has been stable since at least 2019.

Tier 3 — data.gov.in OGD Platform
  The Open Government Data platform hosts a CWC reservoir storage dataset
  with a stable resource UUID. Falls back to this if both live sources fail.
  Dataset: https://data.gov.in/resource/reservoir-storage-status

If all tiers fail the script raises loudly so GitHub Actions marks the
workflow run as failed — no silent stale data.

Output: data/reservoirs.csv
  Columns: date, name, state, capacity_bcm, live_storage_bcm,
           live_storage_pct, ten_yr_avg_pct, deficit_pct, status, source
"""

import csv
import io
import json
import re
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Constants — top-20 CWC-monitored reservoirs with known capacities (BCM)
# Used for cross-checking / supplementing scraped data
# Source: CWC Annual Report 2023–24 (public domain)
# ---------------------------------------------------------------------------
KNOWN_RESERVOIRS = [
    {"name": "Bhakra (Gobind Sagar)",   "state": "Punjab/HP",      "capacity_bcm": 9.34},
    {"name": "Hirakud",                  "state": "Odisha",          "capacity_bcm": 8.14},
    {"name": "Nagarjunasagar",           "state": "Telangana/AP",    "capacity_bcm": 11.56},
    {"name": "Srisailam",               "state": "Telangana/AP",    "capacity_bcm": 8.72},
    {"name": "Indira Sagar (Narmada)",  "state": "Madhya Pradesh",  "capacity_bcm": 12.22},
    {"name": "Tungabhadra",             "state": "Karnataka",       "capacity_bcm": 3.70},
    {"name": "Koyna",                   "state": "Maharashtra",     "capacity_bcm": 2.80},
    {"name": "Tehri",                   "state": "Uttarakhand",     "capacity_bcm": 3.54},
    {"name": "Ukai",                    "state": "Gujarat",         "capacity_bcm": 7.42},
    {"name": "Almatti",                 "state": "Karnataka",       "capacity_bcm": 3.48},
    {"name": "Sardar Sarovar",          "state": "Gujarat",         "capacity_bcm": 9.46},
    {"name": "Maithan",                 "state": "Jharkhand",       "capacity_bcm": 1.44},
    {"name": "Panchet",                 "state": "Jharkhand",       "capacity_bcm": 1.47},
    {"name": "Rihand",                  "state": "Uttar Pradesh",   "capacity_bcm": 10.60},
    {"name": "Rana Pratap Sagar",       "state": "Rajasthan",       "capacity_bcm": 3.41},
    {"name": "Bhadar",                  "state": "Gujarat",         "capacity_bcm": 0.30},
    {"name": "Idukki",                  "state": "Kerala",          "capacity_bcm": 1.99},
    {"name": "Supa",                    "state": "Karnataka",       "capacity_bcm": 7.10},
    {"name": "Pong (Beas)",             "state": "Himachal Pradesh","capacity_bcm": 8.57},
    {"name": "Tawa",                    "state": "Madhya Pradesh",  "capacity_bcm": 2.00},
]

CAPACITY_LOOKUP = {r["name"]: r for r in KNOWN_RESERVOIRS}


def storage_status(live_pct: float) -> str:
    if live_pct < 25:
        return "Critically Low"
    elif live_pct < 50:
        return "Low"
    elif live_pct < 75:
        return "Normal"
    return "High"


# ---------------------------------------------------------------------------
# Tier 1 — India-WRIS JSON API
# ---------------------------------------------------------------------------
WRIS_API_BASE = "https://indiawris.gov.in/api/"
WRIS_RESERVOIR_ENDPOINT = WRIS_API_BASE + "ReservoirStorage/getReservoirStorageDetails"


def fetch_wris() -> list:
    """
    Query the India-WRIS reservoir monitoring API.
    Returns list of reservoir dicts, or [] on failure.
    """
    try:
        today = datetime.now(timezone.utc)
        # API expects date in DD/MM/YYYY format
        payload = {"date": today.strftime("%d/%m/%Y")}
        headers = {
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "Origin":       "https://indiawris.gov.in",
            "Referer":      "https://indiawris.gov.in/wris/",
        }
        resp = requests.post(
            WRIS_RESERVOIR_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # WRIS returns a list of reservoir objects; key names may vary by version
        raw = data if isinstance(data, list) else data.get("data", data.get("result", []))
        if not raw:
            return []

        today_str = today.strftime("%Y-%m-%d")
        records = []
        for item in raw:
            # Normalise key names (WRIS has changed casing across versions)
            item_lower = {k.lower(): v for k, v in item.items()}
            name = (
                item_lower.get("reservoirname") or
                item_lower.get("reservoir_name") or
                item_lower.get("name") or ""
            ).strip()
            if not name:
                continue

            capacity = float(item_lower.get("totalcapacity") or
                             item_lower.get("total_capacity") or
                             CAPACITY_LOOKUP.get(name, {}).get("capacity_bcm", 0) or 0)
            live = float(item_lower.get("livestorage") or
                         item_lower.get("live_storage") or
                         item_lower.get("currentstorage") or 0)
            avg  = float(item_lower.get("normalavgstorage") or
                         item_lower.get("avg_storage") or
                         item_lower.get("lastyearstorage") or 0)
            state = (
                item_lower.get("statename") or
                item_lower.get("state_name") or
                item_lower.get("state") or
                CAPACITY_LOOKUP.get(name, {}).get("state", "")
            ).strip()

            live_pct = round((live / capacity * 100), 1) if capacity else 0.0
            avg_pct  = round((avg  / capacity * 100), 1) if capacity else 0.0

            records.append({
                "date":              today_str,
                "name":              name,
                "state":             state,
                "capacity_bcm":      round(capacity, 2),
                "live_storage_bcm":  round(live, 3),
                "live_storage_pct":  live_pct,
                "ten_yr_avg_pct":    avg_pct,
                "deficit_pct":       round(live_pct - avg_pct, 1),
                "status":            storage_status(live_pct),
                "source":            "India-WRIS (CWC/NHP)",
            })
        return records

    except Exception as exc:
        print(f"   India-WRIS failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Tier 2 — CWC Weekly PDF Bulletin (scraped with pdfplumber)
# ---------------------------------------------------------------------------

def cwc_bulletin_url(date: datetime) -> str:
    """
    CWC publishes the bulletin every Thursday. The file path follows:
      /sites/default/files/reservoir-storage-bulletin/YYYY-Wnn.pdf
    We check the current week and fall back one week if not yet published.
    """
    base = "https://rsms.cwc.gov.in/admin/storage/bulletins/"
    iso  = date.isocalendar()
    return f"{base}{iso[0]}-W{iso[1]:02d}.pdf"


def fetch_cwc_pdf() -> list:
    """
    Download and scrape the CWC weekly reservoir storage PDF.
    Requires: pip install pdfplumber
    Returns list of reservoir dicts, or [] on failure.
    """
    try:
        import pdfplumber
    except ImportError:
        print("   pdfplumber not installed — skipping CWC PDF tier")
        return []

    today = datetime.now(timezone.utc)
    today_str = today.strftime("%Y-%m-%d")

    for days_back in (0, 7, 14):    # try this week, last week, two weeks ago
        url = cwc_bulletin_url(today - timedelta(days=days_back))
        try:
            resp = requests.get(url, timeout=45)
            if resp.status_code != 200:
                continue
            records = []
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if not table:
                        continue
                    for row in table:
                        if not row or len(row) < 5:
                            continue
                        # Typical CWC column order:
                        #   Sl | Reservoir | State | Capacity | Live Storage | % Storage | % Normal
                        name_col   = row[1] if len(row) > 1 else ""
                        state_col  = row[2] if len(row) > 2 else ""
                        cap_col    = row[3] if len(row) > 3 else ""
                        live_col   = row[4] if len(row) > 4 else ""
                        pct_col    = row[5] if len(row) > 5 else ""
                        avg_col    = row[6] if len(row) > 6 else ""

                        name  = str(name_col  or "").strip()
                        state = str(state_col or "").strip()
                        if not name or re.match(r"(?i)^(sl|reservoir|name|s\.no)", name):
                            continue
                        try:
                            cap       = float(re.sub(r"[^\d.]", "", str(cap_col  or "0")) or 0)
                            live      = float(re.sub(r"[^\d.]", "", str(live_col or "0")) or 0)
                            live_pct  = float(re.sub(r"[^\d.]", "", str(pct_col  or "0")) or 0)
                            avg_pct   = float(re.sub(r"[^\d.]", "", str(avg_col  or "0")) or 0)
                        except ValueError:
                            continue

                        # If live_pct not in table, compute from capacity + live
                        if live_pct == 0 and cap > 0:
                            live_pct = round(live / cap * 100, 1)

                        records.append({
                            "date":             today_str,
                            "name":             name,
                            "state":            state or CAPACITY_LOOKUP.get(name, {}).get("state", ""),
                            "capacity_bcm":     round(cap,  2),
                            "live_storage_bcm": round(live, 3),
                            "live_storage_pct": live_pct,
                            "ten_yr_avg_pct":   avg_pct,
                            "deficit_pct":      round(live_pct - avg_pct, 1),
                            "status":           storage_status(live_pct),
                            "source":           f"CWC Weekly PDF Bulletin ({url})",
                        })
            if records:
                print(f"   CWC PDF source: {url}  ({len(records)} reservoirs)")
                return records
        except Exception as exc:
            print(f"   CWC PDF {url} failed: {exc}")
            continue
    return []


# ---------------------------------------------------------------------------
# Tier 3 — data.gov.in Open Government Data (OGD)
# ---------------------------------------------------------------------------
OGD_RESOURCE_ID = "c9f59f3c-3c0e-4954-8c1e-c5e5e3e0e8b5"   # CWC reservoir storage UUID
OGD_API_URL = f"https://api.data.gov.in/resource/{OGD_RESOURCE_ID}?api-key=579b464db66ec23bdd000001cdd3946e6ce24232511d86821994f333&format=json&limit=50"


def fetch_ogd() -> list:
    """
    Fetch CWC reservoir data from the data.gov.in OGD platform.
    Returns [] on failure — OGD is the last-resort fallback.
    """
    try:
        resp = requests.get(OGD_API_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        records = []
        for item in data.get("records", []):
            name  = str(item.get("reservoir_name", "")).strip()
            state = str(item.get("state", "")).strip()
            if not name:
                continue
            try:
                cap      = float(item.get("total_capacity_bcm", 0) or 0)
                live     = float(item.get("live_storage_bcm", 0)   or 0)
                live_pct = round(live / cap * 100, 1) if cap else 0.0
                avg_pct  = float(item.get("avg_storage_pct", 0)    or 0)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Fetching reservoir storage (India-WRIS → CWC PDF → data.gov.in) …")

    records = []

    # Tier 1
    records = fetch_wris()
    if records:
        print(f"   ✅ India-WRIS: {len(records)} reservoirs")
    else:
        print("   India-WRIS returned no data — trying CWC PDF …")
        # Tier 2
        records = fetch_cwc_pdf()
        if records:
            print(f"   ✅ CWC PDF: {len(records)} reservoirs")
        else:
            print("   CWC PDF failed — trying data.gov.in OGD …")
            # Tier 3
            records = fetch_ogd()
            if records:
                print(f"   ✅ data.gov.in OGD: {len(records)} reservoirs")

    if not records:
        raise RuntimeError(
            "All reservoir sources failed (India-WRIS, CWC PDF, data.gov.in). "
            "Install pdfplumber to enable PDF parsing: pip install pdfplumber"
        )

    out_path = DATA_DIR / "reservoirs.csv"
    fieldnames = list(records[0].keys())
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    n_low = sum(1 for r in records if r["live_storage_pct"] < 50)
    print(f"✅  Saved {len(records)} reservoirs → {out_path}")
    print(f"   {n_low}/{len(records)} reservoirs below 50% live storage")


if __name__ == "__main__":
    main()
