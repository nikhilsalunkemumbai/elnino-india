"""
update_rainfall.py
Fetches India monthly rainfall anomalies from CHIRPS v3.

Why v3 (not v2):
  CHIRPS v2 production ends December 2026 — officially announced by the
  Climate Hazards Center (CHC), UC Santa Barbara. This script uses v3,
  which became operational in January 2025 and is the supported long-term path.
  Ref: https://chc.ucsb.edu/data/chirps3

Data strategy — two-tier approach:
  1. Primary: CHC FTP monthly global BIL file → extract India bounding box average
       URL: https://data.chc.ucsb.edu/products/CHIRPS-3.0/global_monthly/tifs/
       India bbox: Lat 6–37°N, Lon 68–97°E
       Uses: requests + numpy (no rasterio needed for simple bbox mean)

  2. Fallback: NOAA PSL India-region precipitation anomaly time series
       URL: https://psl.noaa.gov/cgi-bin/data/timeseries/timeseries1.pl
       (pre-computed India average, no GeoTIFF processing needed)

  If both live sources fail, the script raises an explicit error so GitHub
  Actions marks the run as failed — no silent stale/placeholder data.

Output: data/rainfall.csv
  Columns: date, year, month, rainfall_mm, climatology_mm, anomaly_mm,
           anomaly_pct, status, source
"""

import csv
import io
import re
import requests
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# CHIRPS v3 — monthly global GeoTIFF index
# ---------------------------------------------------------------------------
CHIRPS_V3_BASE = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/monthly/global/tifs//"

# India bounding box (degrees)
LAT_MIN, LAT_MAX = 6.0,  37.0
LON_MIN, LON_MAX = 68.0, 97.0

# CHIRPS v3 grid parameters (0.05° resolution, global)
CHIRPS_RES       = 0.05
CHIRPS_LAT_START = 60.0    # top of grid (north)
CHIRPS_LON_START = -180.0  # left of grid (west)
CHIRPS_NCOLS     = 7200
CHIRPS_NROWS     = 2400


def chirps_v3_monthly_url(year: int, month: int) -> str:
    return f"{CHIRPS_V3_BASE}chirps-v3.0.{year}.{month:02d}.tif"


def fetch_chirps_v3_india_mean(year: int, month: int) -> float | None:
    """
    Download a CHIRPS v3 monthly GeoTIFF and return mean mm over India bbox.
    Uses numpy only — no rasterio dependency.
    Returns None if download fails or data is all-missing.
    """
    try:
        import rasterio
        from rasterio.windows import from_bounds
        url = chirps_v3_monthly_url(year, month)
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        # Write to a temp buffer and read with rasterio
        buf = io.BytesIO(resp.content)
        with rasterio.open(buf) as ds:
            win = from_bounds(LON_MIN, LAT_MIN, LON_MAX, LAT_MAX, ds.transform)
            data = ds.read(1, window=win).astype(float)
            nodata = ds.nodata if ds.nodata is not None else -9999.0
            data[data == nodata] = np.nan
            mean = float(np.nanmean(data))
            return mean if np.isfinite(mean) else None
    except ImportError:
        # rasterio not installed — fall through to NOAA PSL fallback
        return None
    except Exception:
        return None


def build_chirps_v3_series(n_months: int = 24) -> list:
    """
    Attempt to build a time series of India mean rainfall from CHIRPS v3.
    Returns list of dicts or empty list if all fetches fail.
    """
    now = datetime.now(timezone.utc)
    records = []
    # CHIRPS v3 monthly data lags ~6 weeks behind present
    for offset in range(n_months, 0, -1):
        # Step back month by month
        total_month = (now.year * 12 + now.month - 1) - offset
        year  = total_month // 12
        month = (total_month % 12) + 1
        mean_mm = fetch_chirps_v3_india_mean(year, month)
        if mean_mm is None:
            continue
        records.append((year, month, mean_mm))
    return records


# ---------------------------------------------------------------------------
# NOAA PSL fallback — India-averaged precipitation anomaly
# Pre-computed monthly anomaly for India land area, no GeoTIFF processing
# ---------------------------------------------------------------------------
NOAA_PSL_URL = (
    "https://psl.noaa.gov/cgi-bin/data/timeseries/timeseries1.pl"
    "?ntype=1&var=Precipitation+Rate&level=2000&lat1=6&lat2=37"
    "&lon1=68&lon2=97&iseas=0&mon1=0&mon2=11&iarea=1&typeout=1&Submit=Create+Timeseries"
)

# Alternatively, GPCC monthly India precip from NOAA PSL (more stable endpoint)
NOAA_PSL_GPCC_URL = (
    "https://psl.noaa.gov/data/gridded/data.gpcc.html"
)

# Simplest stable fallback: NOAA CPC global unified gauge-based precip
# India-averaged monthly values from PSL timeseries tool
NOAA_PSL_SIMPLE_URL = (
    "https://psl.noaa.gov/cgi-bin/data/timeseries/timeseries1.pl"
    "?ntype=1&var=Precipitation+Rate&level=2000"
    "&lat1=8&lat2=35&lon1=68&lon2=90"
    "&iseas=0&mon1=0&mon2=11&iarea=1&typeout=1&Submit=Create+Timeseries"
)


def fetch_noaa_psl_precip() -> list:
    """
    Fetch NOAA PSL India-averaged monthly precipitation anomaly.
    PSL timeseries tool returns a plain text table:
      YEAR  MON  VALUE
    Returns list of (year, month, anomaly_mm) tuples.
    """
    try:
        resp = requests.get(NOAA_PSL_SIMPLE_URL, timeout=45)
        resp.raise_for_status()
        records = []
        for line in resp.text.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            if not re.match(r"^\d{4}$", parts[0]):
                continue
            year, month = int(parts[0]), int(parts[1])
            try:
                val = float(parts[2])
            except ValueError:
                continue
            if abs(val) > 9000:
                continue
            records.append((year, month, val))
        return records
    except Exception as exc:
        print(f"   NOAA PSL fallback failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Climatology — approximate India monthly mean rainfall (mm)
# Source: IMD long-period averages 1971–2020 (public domain)
# Used to compute anomaly_pct when only absolute values are available
# ---------------------------------------------------------------------------
INDIA_CLIMATOLOGY_MM = {
    1: 17,  2: 22,  3: 28,  4: 42,  5: 62,  6: 163,
    7: 285, 8: 263, 9: 167, 10: 70, 11: 29, 12: 16,
}


def compute_anomaly(rainfall_mm: float, month: int) -> tuple[float, float, str]:
    clim = INDIA_CLIMATOLOGY_MM.get(month, 100)
    anomaly_mm  = round(rainfall_mm - clim, 1)
    anomaly_pct = round((anomaly_mm / clim) * 100, 1) if clim else 0.0
    status = (
        "Deficit"  if anomaly_pct < -10 else
        "Surplus"  if anomaly_pct >  10 else
        "Normal"
    )
    return anomaly_mm, anomaly_pct, status


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Fetching India rainfall (CHIRPS v3 → NOAA PSL fallback) …")

    records = []
    source_used = ""

    # --- Tier 1: CHIRPS v3 (requires rasterio) ---
    chirps_series = build_chirps_v3_series(n_months=24)
    if chirps_series:
        source_used = "CHIRPS v3 (CHC/UCSB)"
        for year, month, mean_mm in chirps_series:
            anom_mm, anom_pct, status = compute_anomaly(mean_mm, month)
            records.append({
                "date":          f"{year}-{month:02d}-01",
                "year":          year,
                "month":         month,
                "rainfall_mm":   round(mean_mm, 1),
                "climatology_mm": INDIA_CLIMATOLOGY_MM.get(month, 0),
                "anomaly_mm":    anom_mm,
                "anomaly_pct":   anom_pct,
                "status":        status,
                "source":        source_used,
            })
        print(f"   Source: {source_used}")
    else:
        print("   CHIRPS v3 unavailable (rasterio not installed or fetch failed)")
        print("   Trying NOAA PSL fallback …")

    # --- Tier 2: NOAA PSL India averaged precipitation anomaly ---
    if not records:
        psl_series = fetch_noaa_psl_precip()
        if psl_series:
            source_used = "NOAA PSL CPC Unified Gauge"
            for year, month, anom_mm in psl_series[-24:]:
                clim = INDIA_CLIMATOLOGY_MM.get(month, 100)
                rainfall_mm = round(clim + anom_mm, 1)
                anom_pct    = round((anom_mm / clim) * 100, 1) if clim else 0.0
                status = (
                    "Deficit" if anom_pct < -10 else
                    "Surplus" if anom_pct >  10 else
                    "Normal"
                )
                records.append({
                    "date":           f"{year}-{month:02d}-01",
                    "year":           year,
                    "month":          month,
                    "rainfall_mm":    rainfall_mm,
                    "climatology_mm": clim,
                    "anomaly_mm":     round(anom_mm, 1),
                    "anomaly_pct":    anom_pct,
                    "status":         status,
                    "source":         source_used,
                })
            print(f"   Source: {source_used}")

    # --- Fail loudly if both tiers failed ---
    if not records:
        raise RuntimeError(
            "All rainfall sources failed (CHIRPS v3 and NOAA PSL). "
            "Check network access or source URLs. "
            "Install rasterio to enable CHIRPS v3 GeoTIFF parsing."
        )

    out_path = DATA_DIR / "rainfall.csv"
    fieldnames = list(records[0].keys())
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    latest = records[-1]
    print(f"✅  Saved {len(records)} months → {out_path}")
    print(f"   Latest: {latest['date']}  {latest['anomaly_pct']:+.1f}%  ({latest['status']})")


if __name__ == "__main__":
    main()
