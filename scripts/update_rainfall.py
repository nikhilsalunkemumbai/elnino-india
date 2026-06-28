"""
update_rainfall.py
Fetches India monthly rainfall anomalies from CHIRPS v3.

Why v3 (not v2):
  CHIRPS v2 production ends December 2026. This script uses v3,
  operational since January 2025.
  Ref: https://chc.ucsb.edu/data/chirps3

Performance fix (Jun 2026):
  Original script downloaded 24 GeoTIFFs every CI run (~5 min).
  Now: reads existing rainfall.csv, only fetches months not already present.
  Typically 1-2 new months per daily CI run → ~30 seconds total.

Data strategy:
  Tier 1: CHC CHIRPS v3 monthly GeoTIFF — extract India bbox mean (requires rasterio)
  Tier 2: NOAA PSL CPC Unified Gauge — India-averaged monthly anomaly (no rasterio)
  Both tiers use incremental fetch: skip months already in rainfall.csv.

Output: src/data/rainfall.csv
  Columns: date, year, month, rainfall_mm, climatology_mm,
           anomaly_mm, anomaly_pct, status, source
"""

import csv
import io
import re
import requests
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, date

DATA_DIR = Path(__file__).parent.parent / "src" / "data"
DATA_DIR.mkdir(exist_ok=True)

CHIRPS_V3_BASE = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/monthly/global/tifs/"

LAT_MIN, LAT_MAX = 6.0,  37.0
LON_MIN, LON_MAX = 68.0, 97.0

NOAA_PSL_SIMPLE_URL = (
    "https://psl.noaa.gov/cgi-bin/data/timeseries/timeseries1.pl"
    "?ntype=1&var=Precipitation+Rate&level=2000"
    "&lat1=8&lat2=35&lon1=68&lon2=90"
    "&iseas=0&mon1=0&mon2=11&iarea=1&typeout=1&Submit=Create+Timeseries"
)

# IMD long-period averages 1971-2020 (mm/month, India area mean)
INDIA_CLIMATOLOGY_MM = {
    1: 17,  2: 22,  3: 28,  4: 42,  5: 62,  6: 163,
    7: 285, 8: 263, 9: 167, 10: 70, 11: 29, 12: 16,
}

FIELDNAMES = [
    "date", "year", "month", "rainfall_mm", "climatology_mm",
    "anomaly_mm", "anomaly_pct", "status", "source",
]


# ─────────────────────────────────────────────────────────────────────────────
# Incremental cache — read existing CSV, return set of (year, month) present
# ─────────────────────────────────────────────────────────────────────────────

def load_existing(path: Path) -> tuple[list, set]:
    """
    Returns (existing_records, existing_keys) where keys = set of (year, month).
    Existing records are kept as-is; only missing months are fetched.
    """
    if not path.exists():
        return [], set()
    records = []
    keys    = set()
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                yr = int(row["year"])
                mo = int(row["month"])
                records.append(row)
                keys.add((yr, mo))
            except (KeyError, ValueError):
                continue
    return records, keys


def months_to_fetch(existing_keys: set, n_months: int = 24) -> list[tuple[int,int]]:
    """
    Return (year, month) pairs for the last n_months that are NOT in existing_keys.
    CHIRPS lags ~6 weeks so we never expect the current month to be available.
    Always re-fetch the last 2 months (they may have been updated by CHC).
    """
    now = datetime.now(timezone.utc)
    result = []
    for offset in range(n_months, 0, -1):
        total = (now.year * 12 + now.month - 1) - offset
        yr    = total // 12
        mo    = (total % 12) + 1
        # Always refresh last 2 months; skip older months already present
        if (yr, mo) not in existing_keys or offset <= 2:
            result.append((yr, mo))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# CHIRPS v3 GeoTIFF fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch_chirps_v3_india_mean(year: int, month: int) -> float | None:
    try:
        import rasterio
        from rasterio.windows import from_bounds
        url  = f"{CHIRPS_V3_BASE}chirps-v3.0.{year}.{month:02d}.tif"
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        buf  = io.BytesIO(resp.content)
        with rasterio.open(buf) as ds:
            win  = from_bounds(LON_MIN, LAT_MIN, LON_MAX, LAT_MAX, ds.transform)
            data = ds.read(1, window=win).astype(float)
            nd   = ds.nodata if ds.nodata is not None else -9999.0
            data[data == nd] = np.nan
            mean = float(np.nanmean(data))
            return mean if np.isfinite(mean) else None
    except ImportError:
        return None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# NOAA PSL fallback
# ─────────────────────────────────────────────────────────────────────────────

def fetch_noaa_psl_precip() -> list[tuple[int,int,float]]:
    try:
        resp = requests.get(NOAA_PSL_SIMPLE_URL, timeout=45)
        resp.raise_for_status()
        records = []
        for line in resp.text.splitlines():
            parts = line.split()
            if len(parts) < 3 or not re.match(r"^\d{4}$", parts[0]):
                continue
            try:
                yr, mo, val = int(parts[0]), int(parts[1]), float(parts[2])
            except ValueError:
                continue
            if abs(val) > 9000:
                continue
            records.append((yr, mo, val))
        return records
    except Exception as exc:
        print(f"   NOAA PSL fallback failed: {exc}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def compute_anomaly(rainfall_mm: float, month: int) -> tuple[float, float, str]:
    clim       = INDIA_CLIMATOLOGY_MM.get(month, 100)
    anom_mm    = round(rainfall_mm - clim, 1)
    anom_pct   = round((anom_mm / clim) * 100, 1) if clim else 0.0
    status     = ("Deficit" if anom_pct < -10 else "Surplus" if anom_pct > 10 else "Normal")
    return anom_mm, anom_pct, status


def make_record(year, month, rainfall_mm, source) -> dict:
    clim = INDIA_CLIMATOLOGY_MM.get(month, 0)
    anom_mm, anom_pct, status = compute_anomaly(rainfall_mm, month)
    return {
        "date":           f"{year}-{month:02d}-01",
        "year":           year,
        "month":          month,
        "rainfall_mm":    round(rainfall_mm, 1),
        "climatology_mm": clim,
        "anomaly_mm":     anom_mm,
        "anomaly_pct":    anom_pct,
        "status":         status,
        "source":         source,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Fetching India rainfall (CHIRPS v3 → NOAA PSL fallback) …")

    out_path = DATA_DIR / "rainfall.csv"
    existing_records, existing_keys = load_existing(out_path)
    need = months_to_fetch(existing_keys, n_months=24)

    print(f"   Existing: {len(existing_keys)} months in cache")
    print(f"   To fetch: {len(need)} months  {need[:3]}{'…' if len(need)>3 else ''}")

    new_records = {}  # (year,month) -> record

    # ── Tier 1: CHIRPS v3 ─────────────────────────────────────────────────
    chirps_ok = 0
    for yr, mo in need:
        mean_mm = fetch_chirps_v3_india_mean(yr, mo)
        if mean_mm is not None:
            new_records[(yr, mo)] = make_record(yr, mo, mean_mm, "CHIRPS v3 (CHC/UCSB)")
            chirps_ok += 1

    if chirps_ok:
        print(f"   CHIRPS v3: {chirps_ok}/{len(need)} months fetched")

    # ── Tier 2: NOAA PSL for any remaining missing months ─────────────────
    still_missing = [m for m in need if m not in new_records]
    if still_missing:
        print(f"   NOAA PSL fallback for {len(still_missing)} months …")
        psl = fetch_noaa_psl_precip()
        psl_by_key = {(yr, mo): anom for yr, mo, anom in psl}
        for yr, mo in still_missing:
            if (yr, mo) in psl_by_key:
                clim  = INDIA_CLIMATOLOGY_MM.get(mo, 100)
                rain  = clim + psl_by_key[(yr, mo)]
                new_records[(yr, mo)] = make_record(yr, mo, rain, "NOAA PSL CPC Unified Gauge")

    # ── Merge: existing (keep) + new (add/overwrite last 2 months) ─────────
    merged = {}
    for row in existing_records:
        key = (int(row["year"]), int(row["month"]))
        merged[key] = row

    for key, rec in new_records.items():
        merged[key] = rec  # overwrites stale last-2-months records

    # Keep last 24 months only, sorted chronologically
    sorted_keys = sorted(merged.keys())[-24:]
    final = [merged[k] for k in sorted_keys]

    if not final:
        raise RuntimeError(
            "All rainfall sources failed (CHIRPS v3 and NOAA PSL). "
            "Check network access or source URLs."
        )

    # ── Write ──────────────────────────────────────────────────────────────
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(final)

    latest = final[-1]
    src    = latest["source"]
    print(f"✅  Saved {len(final)} months → {out_path}")
    print(f"   Latest: {latest['date']}  {float(latest['anomaly_pct']):+.1f}%  ({latest['status']})")
    print(f"   Source: {src}")
    if chirps_ok == 0 and not still_missing:
        print("   ℹ️  All months were cached — no new fetches needed")


if __name__ == "__main__":
    main()
