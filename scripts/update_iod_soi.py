"""
update_iod_soi.py
Fetches:
  - IOD / DMI  — NOAA PSL HadISST-derived DMI (confirmed live June 2026)
  - SOI        — NOAA CPC standardised SOI

Fixes applied (from Actions log analysis June 2026):
  - IOD: BOM URL → NOAA PSL /data/timeseries/month/data/dmi.had.long.data ✅
  - SOI: parser was broken — NOAA CPC SOI file format is:
      Header: "(STAND TAHITI - STAND DARWIN) SEA LEVEL PRESS ANOMALY"
      Header: "YEAR JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC"
      Data:   "1951 2.5 1.5 -0.2 ..." (year + 12 values on ONE line, 13 parts)
    Old parser looked for a lone year on its own line — never matched → 0 records.
    Fixed: parse lines with 13 parts where first part is a 4-digit year.
  - SOI fallback: NOAA PSL SOI added as secondary source.

Outputs: data/iod.json, data/soi.json
"""

import csv
import io
import json
import re
import requests
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / "src" / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── IOD sources (confirmed live June 2026 via psl.noaa.gov/data/timeseries/month/DMI/) ──
NOAA_DMI_DATA_URL = "https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.data"
NOAA_DMI_CSV_URL  = "https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.csv"

# ── SOI sources ──
# Primary: NOAA CPC standardised SOI (Tahiti - Darwin)
#   Format: 13 columns per data line → YEAR JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC
#   Missing sentinel: -999.9
NOAA_CPC_SOI_URL  = "https://www.cpc.ncep.noaa.gov/data/indices/soi"
# Fallback: NOAA PSL SOI (same index, PSL standard format)
NOAA_PSL_SOI_URL  = "https://psl.noaa.gov/data/timeseries/month/data/soi.data"


# ─────────────────────────────────────────────────────────────────────────────
# IOD helpers
# ─────────────────────────────────────────────────────────────────────────────

def iod_label(dmi: float) -> str:
    if dmi >= 0.4:  return "Positive IOD"
    if dmi <= -0.4: return "Negative IOD"
    return "Neutral IOD"


def parse_noaa_dmi_data(text: str) -> list:
    """
    NOAA PSL standard format:
      First line: start_year  num_years   (skip)
      Data rows:  YYYY  v1  v2 ... v12   (13 parts)
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
            if val <= -99.0:
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
    records = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            year  = int(row.get("Year",  row.get("year",  0)))
            month = int(row.get("Month", row.get("month", 0)))
            val   = float(row.get("DMI", row.get("dmi",   row.get("Value", 0))))
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


# ─────────────────────────────────────────────────────────────────────────────
# SOI helpers
# ─────────────────────────────────────────────────────────────────────────────

def soi_label(soi: float) -> str:
    if soi <= -7: return "El Niño signal"
    if soi >= 7:  return "La Niña signal"
    return "Neutral"


def parse_cpc_soi(text: str) -> list:
    """
    NOAA CPC SOI format (confirmed from actual file, June 2026):
      Line 1: "(STAND TAHITI - STAND DARWIN) SEA LEVEL PRESS ANOMALY"
      Line 2: "YEAR JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC"
      Data:   "1951 2.5 1.5 -0.2 -0.5 ..."  ← 13 parts: year + 12 monthly values
      Missing: -999.9

    BUG FIXED: old parser split on a lone 4-digit year line then expected
    the 12 values on the NEXT line — but CPC puts year + values on ONE line.
    """
    records = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 13:
            continue
        # First part must be a 4-digit year
        if not re.match(r"^\d{4}$", parts[0]):
            continue
        year = int(parts[0])
        for i, val_str in enumerate(parts[1:], start=1):
            try:
                val = float(val_str)
            except ValueError:
                continue
            if abs(val) >= 999:   # missing sentinel
                continue
            records.append({
                "date":  f"{year}-{i:02d}-01",
                "year":  year,
                "month": i,
                "soi":   round(val, 1),
                "label": soi_label(val),
            })
    return records


def parse_psl_soi(text: str) -> list:
    """
    NOAA PSL SOI standard format — same structure as DMI .data:
      YYYY  v1  v2 ... v12  (13 parts per row)
      Missing: -99.9 or -999.9
    """
    records = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 13:
            continue
        if not re.match(r"^\d{4}$", parts[0]):
            continue
        year = int(parts[0])
        for i, val_str in enumerate(parts[1:], start=1):
            try:
                val = float(val_str)
            except ValueError:
                continue
            if val <= -99.0:
                continue
            records.append({
                "date":  f"{year}-{i:02d}-01",
                "year":  year,
                "month": i,
                "soi":   round(val, 1),
                "label": soi_label(val),
            })
    return records


def fetch_soi() -> list:
    for url, parser, label in [
        (NOAA_CPC_SOI_URL, parse_cpc_soi, "NOAA CPC SOI"),
        (NOAA_PSL_SOI_URL, parse_psl_soi, "NOAA PSL SOI"),
    ]:
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            records = parser(resp.text)
            if records:
                print(f"   SOI source: {label}  ({len(records)} records)")
                return records
            print(f"   {label} returned 0 records — trying next …")
        except Exception as exc:
            print(f"   {label} failed: {exc} — trying next …")
    raise RuntimeError(
        "All SOI sources failed.\n"
        f"  Tried: {NOAA_CPC_SOI_URL}\n"
        f"  Tried: {NOAA_PSL_SOI_URL}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    print("Fetching IOD/DMI …")
    iod_records = fetch_iod()
    latest_iod  = iod_records[-1] if iod_records else {}
    (DATA_DIR / "iod.json").write_text(json.dumps({
        "updated":    now,
        "source":     NOAA_DMI_DATA_URL,
        "note":       "NOAA PSL HadISST1.1 DMI. Ref: psl.noaa.gov/data/timeseries/month/DMI/",
        "latest":     latest_iod,
        "timeseries": iod_records,
    }, indent=2))
    print(f"✅  Saved {len(iod_records)} IOD records → data/iod.json")
    if latest_iod:
        print(f"   Latest DMI: {latest_iod.get('dmi', 'N/A'):+.3f}°C  ({latest_iod.get('label', '')})")

    print("Fetching SOI (NOAA CPC → NOAA PSL fallback) …")
    soi_records = fetch_soi()
    latest_soi  = soi_records[-1] if soi_records else {}
    (DATA_DIR / "soi.json").write_text(json.dumps({
        "updated":    now,
        "source":     NOAA_CPC_SOI_URL,
        "latest":     latest_soi,
        "timeseries": soi_records,
    }, indent=2))
    print(f"✅  Saved {len(soi_records)} SOI records → data/soi.json")
    if latest_soi:
        print(f"   Latest SOI: {latest_soi.get('soi', 'N/A'):+.1f}  ({latest_soi.get('label', '')})")


if __name__ == "__main__":
    main()
