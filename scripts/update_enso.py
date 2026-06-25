"""
update_enso.py
Fetches Nino3.4 SST anomaly data from NOAA CPC.

Sources:
  Primary:    NOAA CPC ONI (3-month running mean, ~2 month lag by design)
              https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt
  Supplement: NOAA CPC weekly Nino3.4 SST (1-week lag)
              https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for
              Added Jun 2026: ONI lags real-world conditions by 1-2 months due
              to 3-month smoothing. The weekly index shows current conditions
              (Jun 17 2026: +1.7C vs ONI showing +0.48C for MAM 2026).
              Weekly value is stored separately and displayed on dashboard
              as "current week" — stress index continues using ONI for stability.

Output: src/data/nino34.json
  {
    updated, source,
    latest: { date, season, year, month, anomaly, el_nino, la_nina, label },
    latest_weekly: { date, anomaly, label } | null,
    timeseries: [ ...ONI records... ]
  }
"""

import json
import re
import requests
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / "src" / "data"
DATA_DIR.mkdir(exist_ok=True)

ONI_URL    = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
WEEKLY_URL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"

MONTH_LABELS = {
    "DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
    "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12,
}


def el_nino_label(anomaly: float) -> str:
    if anomaly >= 2.0:  return "Strong El Niño"
    if anomaly >= 1.5:  return "Moderate El Niño"
    if anomaly >= 0.8:  return "Weak El Niño"
    if anomaly <= -2.0: return "Strong La Niña"
    if anomaly <= -1.5: return "Moderate La Niña"
    if anomaly <= -0.8: return "Weak La Niña"
    return "Neutral"


def fetch_oni() -> list:
    """Parse NOAA CPC ONI table — 3-month running mean."""
    resp = requests.get(ONI_URL, timeout=30)
    resp.raise_for_status()
    records = []
    for line in resp.text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        season, year, _, anom = parts[0], parts[1], parts[2], parts[3]
        if not re.match(r"^-?\d+\.\d+$", anom):
            continue
        month = MONTH_LABELS.get(season, 1)
        records.append({
            "date":    f"{year}-{month:02d}-01",
            "season":  season,
            "year":    int(year),
            "month":   month,
            "anomaly": float(anom),
            "el_nino": float(anom) >= 0.8,
            "la_nina": float(anom) <= -0.8,
        })
    return records


def fetch_weekly_nino34() -> dict | None:
    """
    Parse NOAA CPC weekly Nino3.4 SST anomaly file (wksst9120.for).

    Fixed-width format (confirmed from file structure):
      Week  Nino1+2   ANOM    Nino3     ANOM    Nino4     ANOM   Nino3.4   ANOM
      04JAN1990  23.24  -0.67  25.87  -0.37  28.12   0.24  26.39   0.21

    Nino3.4 anomaly is in the LAST column (column 9, 0-indexed).
    Date format: DDMMMYYYY (e.g. 04JAN1990, 18JUN2026)
    """
    try:
        resp = requests.get(WEEKLY_URL, timeout=30)
        resp.raise_for_status()
        records = []
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            # Date token: 8-char DDMMMYYYY
            m = re.match(r"^(\d{2})([A-Z]{3})(\d{4})\s+([\d.\s-]+)$", line)
            if not m:
                continue
            day_str, mon_str, year_str = m.group(1), m.group(2), m.group(3)
            values_str = m.group(4).strip()
            values = values_str.split()
            if len(values) < 8:
                continue
            # Column layout: n12_sst n12_anom n3_sst n3_anom n4_sst n4_anom n34_sst n34_anom
            try:
                nino34_anom = float(values[7])
            except (ValueError, IndexError):
                continue
            if abs(nino34_anom) > 10:  # sanity check
                continue
            MONTHS = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                      "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
            month = MONTHS.get(mon_str)
            if not month:
                continue
            records.append({
                "date":    f"{year_str}-{month:02d}-{day_str}",
                "year":    int(year_str),
                "month":   month,
                "anomaly": round(nino34_anom, 2),
            })

        if not records:
            print("   Weekly Nino3.4: 0 records parsed")
            return None

        # Sort chronologically — wksst file may have footer lines that
        # cause out-of-order appends; take the genuinely most recent date
        records.sort(key=lambda r: r["date"])
        latest = records[-1]
        result = {
            "date":    latest["date"],
            "anomaly": latest["anomaly"],
            "label":   el_nino_label(latest["anomaly"]),
            "source":  WEEKLY_URL,
            "note":    "Weekly Nino3.4 OISST anomaly (1-week lag). Higher resolution than ONI 3-month mean.",
        }
        print(f"   Weekly Nino3.4: latest {result['date']} → {result['anomaly']:+.2f}°C "
              f"({result['label']})")
        return result

    except Exception as exc:
        print(f"   Weekly Nino3.4 failed: {exc}")
        return None


def main():
    print("Fetching NOAA ONI data …")
    records = fetch_oni()
    if not records:
        raise RuntimeError("No ONI records parsed — check NOAA URL or format.")

    latest = records[-1]
    latest_label = el_nino_label(latest["anomaly"])
    print(f"   ONI latest: {latest['season']} {latest['year']} → "
          f"{latest['anomaly']:+.2f}°C ({latest_label})")

    print("Fetching weekly Nino3.4 supplement …")
    weekly = fetch_weekly_nino34()

    output = {
        "updated":       datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source":        ONI_URL,
        "weekly_source": WEEKLY_URL,
        "note":          (
            "ONI (3-month running mean) is the stress index input. "
            "latest_weekly shows current-week Nino3.4 for dashboard context — "
            "it typically reads 1-2C higher than ONI during rapid El Nino onset."
        ),
        "latest": {
            **latest,
            "label": latest_label,
        },
        "latest_weekly": weekly,
        "timeseries": records,
    }

    out_path = DATA_DIR / "nino34.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"✅  Saved {len(records)} ONI records → {out_path}")
    if weekly:
        print(f"   Weekly supplement: {weekly['date']} → {weekly['anomaly']:+.2f}°C")

if __name__ == "__main__":
    main()
