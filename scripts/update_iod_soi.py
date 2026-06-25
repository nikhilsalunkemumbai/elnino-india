"""
update_iod_soi.py
Fetches:
  - IOD / DMI  — JAMSTEC monthly (primary) + JAMSTEC weekly (supplement)
                 NOAA PSL HadISST as final fallback (stale after Jan 2025)
  - SOI        — NOAA CPC standardised SOI (primary) + NOAA PSL fallback

Source change log:
  Jun 2026 v1: BOM URL → NOAA PSL dmi.had.long.data
  Jun 2026 v2: NOAA PSL IOD confirmed stale (stops Jan 2025, file not updated)
               → Primary switched to JAMSTEC monthly HadISST DMI (current to present)
               → JAMSTEC weekly OISST DMI added as supplement (1-2 week lag)
               SOI partial-year parser bug fixed: 2026 rows have <13 valid values
               (future months = -999.9 sentinel) so parser was skipping the whole row.
               Fix: accept any row where parts[0] is a 4-digit year regardless of
               total column count, then filter sentinels per month as usual.

IOD source priority:
  Tier 1: JAMSTEC monthly HadISST DMI  (1870-present, same methodology as PSL)
           https://www.jamstec.go.jp/aplinfo/sintexf/DATA/dmi.monthly.txt
  Tier 2: JAMSTEC weekly OISST DMI     (Nov 1981-present, 1-2 week lag)
           https://www.jamstec.go.jp/aplinfo/sintexf/DATA/dmi.weekly.txt
  Tier 3: NOAA PSL HadISST .data       (stale after Jan 2025, emergency fallback)
           https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.data

SOI source priority:
  Tier 1: NOAA CPC SOI                 (www.cpc.ncep.noaa.gov/data/indices/soi)
  Tier 2: NOAA PSL SOI                 (psl.noaa.gov/data/timeseries/month/data/soi.data)

Outputs: src/data/iod.json, src/data/soi.json
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

# ── IOD sources ───────────────────────────────────────────────────────────────
JAMSTEC_DMI_MONTHLY = "https://www.jamstec.go.jp/aplinfo/sintexf/DATA/dmi.monthly.txt"
JAMSTEC_DMI_WEEKLY  = "https://www.jamstec.go.jp/aplinfo/sintexf/DATA/dmi.weekly.txt"
NOAA_PSL_DMI_DATA   = "https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.data"

# ── SOI sources ───────────────────────────────────────────────────────────────
NOAA_CPC_SOI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/soi"
NOAA_PSL_SOI_URL = "https://psl.noaa.gov/data/timeseries/month/data/soi.data"


# ─────────────────────────────────────────────────────────────────────────────
# IOD helpers
# ─────────────────────────────────────────────────────────────────────────────

def iod_label(dmi: float) -> str:
    if dmi >= 0.4:  return "Positive IOD"
    if dmi <= -0.4: return "Negative IOD"
    return "Neutral IOD"


def parse_jamstec_monthly(text: str) -> list:
    """
    JAMSTEC monthly DMI format (HadISST-based, 1870-present):
      Lines with: YYYY/MM  value
      e.g.  1870/ 1  -0.074
            1870/ 2   0.015
    Missing/fill: 999.000 or similar large values
    """
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: YYYY/MM  value  (possibly extra columns)
        m = re.match(r"^(\d{4})\s*/\s*(\d{1,2})\s+([-\d.]+)", line)
        if not m:
            continue
        year, month, val_str = int(m.group(1)), int(m.group(2)), m.group(3)
        try:
            val = float(val_str)
        except ValueError:
            continue
        if abs(val) > 90:  # missing sentinel
            continue
        records.append({
            "date":   f"{year}-{month:02d}-01",
            "year":   year,
            "month":  month,
            "dmi":    round(val, 3),
            "label":  iod_label(val),
            "source": "JAMSTEC HadISST monthly",
        })
    return records


def parse_jamstec_weekly(text: str) -> list:
    """
    JAMSTEC weekly DMI format (NOAA OISST v2, Nov 1981-present):
      Lines with: YYYY/MM/DD  value
      e.g.  1981/11/04   0.123
    Use as supplement: convert to approximate monthly by taking last weekly
    value per month when more recent than the monthly record.
    Missing: 999.000
    """
    weekly = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\d{4})/(\d{2})/(\d{2})\s+([-\d.]+)", line)
        if not m:
            continue
        year, month, day, val_str = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        try:
            val = float(val_str)
        except ValueError:
            continue
        if abs(val) > 90:
            continue
        weekly.append({
            "year": year, "month": month, "day": day,
            "dmi": round(val, 3),
        })
    if not weekly:
        return []
    # Convert to monthly: use the last weekly reading in each month
    by_month = {}
    for r in weekly:
        key = (r["year"], r["month"])
        by_month[key] = r  # last one wins (chronological)
    records = []
    for (year, month), r in sorted(by_month.items()):
        records.append({
            "date":   f"{year}-{month:02d}-01",
            "year":   year,
            "month":  month,
            "dmi":    r["dmi"],
            "label":  iod_label(r["dmi"]),
            "source": "JAMSTEC OISST weekly (latest per month)",
        })
    return records


def parse_noaa_psl_dmi(text: str) -> list:
    """
    NOAA PSL standard format (stale after Jan 2025, emergency fallback only):
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
                "source": "NOAA PSL HadISST (stale after Jan 2025)",
            })
    return records


def merge_iod_records(monthly: list, weekly: list) -> list:
    """
    Merge monthly (primary) and weekly (supplement) records.
    Weekly records are only used when they extend beyond the monthly dataset.
    Both are deduplicated by (year, month) — monthly takes precedence.
    """
    by_key = {}
    for r in monthly:
        by_key[(r["year"], r["month"])] = r
    # Add weekly records only for months not in the monthly dataset
    if weekly:
        monthly_latest = max(by_key.keys()) if by_key else (0, 0)
        for r in weekly:
            key = (r["year"], r["month"])
            if key > monthly_latest:
                by_key[key] = r
    return [v for _, v in sorted(by_key.items())]


def fetch_iod() -> list:
    """
    Try JAMSTEC monthly → supplement with JAMSTEC weekly for recent months
    → fall back to NOAA PSL (stale) if JAMSTEC unavailable.
    """
    monthly_records = []
    weekly_records  = []

    # Tier 1: JAMSTEC monthly
    try:
        resp = requests.get(JAMSTEC_DMI_MONTHLY, timeout=30)
        resp.raise_for_status()
        monthly_records = parse_jamstec_monthly(resp.text)
        if monthly_records:
            print(f"   IOD Tier 1 (JAMSTEC monthly): {len(monthly_records)} records, "
                  f"latest {monthly_records[-1]['date']}")
        else:
            print("   IOD Tier 1 (JAMSTEC monthly): 0 records parsed — trying next")
    except Exception as exc:
        print(f"   IOD Tier 1 (JAMSTEC monthly) failed: {exc}")

    # Tier 2: JAMSTEC weekly — always try, use to extend monthly
    try:
        resp = requests.get(JAMSTEC_DMI_WEEKLY, timeout=30)
        resp.raise_for_status()
        weekly_records = parse_jamstec_weekly(resp.text)
        if weekly_records:
            print(f"   IOD Tier 2 (JAMSTEC weekly):  {len(weekly_records)} monthly-equiv records, "
                  f"latest {weekly_records[-1]['date']}")
        else:
            print("   IOD Tier 2 (JAMSTEC weekly): 0 records parsed")
    except Exception as exc:
        print(f"   IOD Tier 2 (JAMSTEC weekly) failed: {exc}")

    # Merge monthly + weekly
    if monthly_records or weekly_records:
        merged = merge_iod_records(monthly_records, weekly_records)
        if merged:
            print(f"   IOD merged: {len(merged)} records, latest {merged[-1]['date']}")
            return merged

    # Tier 3: NOAA PSL fallback (stale)
    print("   IOD Tier 3 (NOAA PSL — stale after Jan 2025): attempting ...")
    try:
        resp = requests.get(NOAA_PSL_DMI_DATA, timeout=30)
        resp.raise_for_status()
        records = parse_noaa_psl_dmi(resp.text)
        if records:
            print(f"   IOD Tier 3: {len(records)} records, latest {records[-1]['date']} (stale)")
            return records
    except Exception as exc:
        print(f"   IOD Tier 3 failed: {exc}")

    raise RuntimeError(
        "All IOD sources failed.\n"
        f"  JAMSTEC monthly: {JAMSTEC_DMI_MONTHLY}\n"
        f"  JAMSTEC weekly:  {JAMSTEC_DMI_WEEKLY}\n"
        f"  NOAA PSL:        {NOAA_PSL_DMI_DATA}"
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
    NOAA CPC SOI format:
      Header lines (skip anything without a 4-digit year as first token)
      Data: YEAR JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC
            "1951 2.5 1.5 -0.2 ..."
      Missing sentinel: -999.9

    BUG FIX v2 (Jun 2026):
      Old parser required exactly 13 parts per row.
      Current-year partial rows (e.g. 2026 with only Jan-May filled, rest -999.9)
      still have 13 columns but the parser filtered all -999.9 values and
      then produced 0 valid months — but the whole row was still accepted.
      The real issue: in some CPC file versions the partial year row has FEWER
      than 13 columns (only filled months listed). Accept any row where
      parts[0] is a 4-digit year 1950-2099, regardless of column count.
      Then extract only non-sentinel monthly values by position.
    """
    MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
    records = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        # First token must be a 4-digit year in plausible range
        if not re.match(r"^\d{4}$", parts[0]):
            continue
        year = int(parts[0])
        if not (1950 <= year <= 2099):
            continue
        # Extract monthly values positionally (index 1..12)
        for i, val_str in enumerate(parts[1:13], start=1):
            try:
                val = float(val_str)
            except ValueError:
                continue
            if abs(val) >= 999:
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
    """NOAA PSL SOI — same 13-column format as DMI .data files."""
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
                print(f"   SOI source: {label}  ({len(records)} records, "
                      f"latest {records[-1]['date']})")
                return records
            print(f"   {label}: 0 records — trying next")
        except Exception as exc:
            print(f"   {label} failed: {exc} — trying next")
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
        "source":     JAMSTEC_DMI_MONTHLY,
        "note":       "Primary: JAMSTEC HadISST monthly DMI. Supplement: JAMSTEC OISST weekly DMI for recent months. Fallback: NOAA PSL (stale after Jan 2025).",
        "latest":     latest_iod,
        "timeseries": iod_records,
    }, indent=2))
    print(f"✅  Saved {len(iod_records)} IOD records → data/iod.json")
    if latest_iod:
        print(f"   Latest DMI: {latest_iod.get('dmi', 'N/A'):+.3f}°C  "
              f"({latest_iod.get('label', '')})  [{latest_iod.get('source','')}]")

    print("Fetching SOI …")
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
        print(f"   Latest SOI: {latest_soi.get('soi', 'N/A'):+.1f}  "
              f"({latest_soi.get('label', '')})")


if __name__ == "__main__":
    main()
