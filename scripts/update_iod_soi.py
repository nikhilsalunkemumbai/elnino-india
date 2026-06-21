"""
update_iod_soi.py
Fetches:
  - Indian Ocean Dipole (IOD / DMI) — primary: NOAA PSL HadISST-derived DMI
                                       fallback: NOAA OSMC state-of-ocean DMI
  - Southern Oscillation Index (SOI) — NOAA CPC (permanent, unchanged)

IOD source history:
  - Original (broken): bom.gov.au/climate/enso/iod_1997.txt  → BOM changed URLs
  - v2 attempt (broken): psl.noaa.gov/data/correlation/dmi.data  → 404, path moved
  - v3 attempt (broken): jamstec.go.jp/aplinfo/sintexf/iod/iod_index_ersstv5.txt → 404
  - CURRENT (confirmed live June 2026):
      Primary:  https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.data
      Fallback: https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.csv

Outputs:
  data/iod.json
  data/soi.json
"""

import csv
import io
import json
import re
import requests
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# --- IOD / DMI sources ---
# Confirmed live: https://psl.noaa.gov/data/timeseries/month/DMI/
NOAA_DMI_DATA_URL = "https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.data"
NOAA_DMI_CSV_URL  = "https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.csv"

# --- SOI source (unchanged — NOAA CPC is permanent) ---
SOI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/soi"


# ---------------------------------------------------------------------------
# IOD helpers
# ---------------------------------------------------------------------------

def iod_label(dmi: float) -> str:
    if dmi >= 0.4:
        return "Positive IOD"
    elif dmi <= -0.4:
        return "Negative IOD"
    return "Neutral IOD"


def parse_noaa_dmi_data(text: str) -> list:
    """
    NOAA PSL standard format:
      First line: start_year  num_years
      Then rows:  YYYY  v1  v2 ... v12
      Missing sentinel: -999.9 or -99.99
    """
    records = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 13:
            continue
        if not re.match(r"^\d{4}$", parts[0]):
            continue
        year = int(parts[0])
        for month_idx, val_str in enumerate(parts[1:], start=1):
            try:
                val = float(val_str)
            except ValueError:
                continue
            if val <= -99.0:   # missing sentinel
                continue
            records.append({
                "date":   f"{year}-{month_idx:02d}-01",
                "year":   year,
                "month":  month_idx,
                "dmi":    round(val, 3),
                "label":  iod_label(val),
                "source": "NOAA PSL HadISST1.1",
            })
    return records


def parse_noaa_dmi_csv(text: str) -> list:
    """Parse the CSV version of the same PSL DMI file."""
    records = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            year  = int(row.get("Year", row.get("year", 0)))
            month = int(row.get("Month", row.get("month", 0)))
            val   = float(row.get("DMI", row.get("dmi", row.get("Value", 0))))
        except (ValueError, TypeError):
            continue
        if abs(val) > 90:
            continue
        records.append({
            "date":   f"{year}-{month:02d}-01",
            "year":   year,
            "month":  month,
            "dmi":    round(val, 3),
            "label":  iod_label(val),
            "source": "NOAA PSL HadISST1.1 (CSV)",
        })
    return records


def fetch_iod() -> list:
    """Try .data format first, then CSV format."""
    for url, parser, label in [
        (NOAA_DMI_DATA_URL, parse_noaa_dmi_data, "NOAA PSL DMI .data"),
        (NOAA_DMI_CSV_URL,  parse_noaa_dmi_csv,  "NOAA PSL DMI .csv"),
    ]:
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            records = parser(resp.text)
            if records:
                print(f"   IOD source: {label}")
                return records
            print(f"   {label} returned 0 records — trying next …")
        except Exception as exc:
            print(f"   {label} failed: {exc} — trying next …")
    raise RuntimeError(
        "All IOD sources failed.\n"
        f"  Tried: {NOAA_DMI_DATA_URL}\n"
        f"  Tried: {NOAA_DMI_CSV_URL}\n"
        "Check https://psl.noaa.gov/data/timeseries/month/DMI/ for current URLs."
    )


# ---------------------------------------------------------------------------
# SOI helpers
# ---------------------------------------------------------------------------

def soi_label(soi: float) -> str:
    if soi <= -7:
        return "El Niño signal"
    elif soi >= 7:
        return "La Niña signal"
    return "Neutral"


def fetch_soi() -> list:
    resp = requests.get(SOI_URL, timeout=30)
    resp.raise_for_status()
    records = []
    current_year = None
    for line in resp.text.splitlines():
        parts = line.split()
        if len(parts) == 1 and re.match(r"^\d{4}$", parts[0]):
            current_year = int(parts[0])
        elif current_year and len(parts) == 12:
            for i, val in enumerate(parts):
                if val in ("-999.9", "999.9"):
                    continue
                try:
                    soi_val = float(val)
                    records.append({
                        "date":  f"{current_year}-{i+1:02d}-01",
                        "year":  current_year,
                        "month": i + 1,
                        "soi":   soi_val,
                        "label": soi_label(soi_val),
                    })
                except ValueError:
                    pass
    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    print("Fetching IOD/DMI …")
    iod_records = fetch_iod()
    latest_iod  = iod_records[-1] if iod_records else {}
    iod_path    = DATA_DIR / "iod.json"
    iod_path.write_text(json.dumps({
        "updated":    now,
        "source":     NOAA_DMI_DATA_URL,
        "note":       "NOAA PSL HadISST1.1 DMI. Ref: psl.noaa.gov/data/timeseries/month/DMI/",
        "latest":     latest_iod,
        "timeseries": iod_records,
    }, indent=2))
    print(f"✅  Saved {len(iod_records)} IOD records → {iod_path}")
    if latest_iod:
        print(f"   Latest DMI: {latest_iod.get('dmi', 'N/A'):+.3f}°C  ({latest_iod.get('label', '')})")

    print("Fetching SOI (NOAA CPC) …")
    soi_records = fetch_soi()
    latest_soi  = soi_records[-1] if soi_records else {}
    soi_path    = DATA_DIR / "soi.json"
    soi_path.write_text(json.dumps({
        "updated":    now,
        "source":     SOI_URL,
        "latest":     latest_soi,
        "timeseries": soi_records,
    }, indent=2))
    print(f"✅  Saved {len(soi_records)} SOI records → {soi_path}")
    if latest_soi:
        print(f"   Latest SOI: {latest_soi.get('soi', 'N/A'):+.1f}  ({latest_soi.get('label', '')})")


if __name__ == "__main__":
    main()