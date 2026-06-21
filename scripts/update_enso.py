"""
update_enso.py
Fetches the latest Niño3.4 SST anomaly (ONI) from NOAA CPC and saves to data/nino34.json.

Source:
  https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt
  https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/nino34.long.anom.data
"""

import json
import re
import requests
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# NOAA CPC ONI text file — 3-month rolling average Niño3.4 anomaly
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"

MONTH_LABELS = {
    "DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
    "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12,
}

def fetch_oni():
    """Download and parse NOAA ONI table."""
    resp = requests.get(ONI_URL, timeout=30)
    resp.raise_for_status()
    records = []
    for line in resp.text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        season, year, total, anom = parts[0], parts[1], parts[2], parts[3]
        if not re.match(r"^-?\d+\.\d+$", anom):
            continue
        month = MONTH_LABELS.get(season, 1)
        records.append({
            "date": f"{year}-{month:02d}-01",
            "season": season,
            "year": int(year),
            "month": month,
            "anomaly": float(anom),
            "el_nino": float(anom) >= 0.8,
            "la_nina": float(anom) <= -0.8,
        })
    return records

def el_nino_label(anomaly: float) -> str:
    if anomaly >= 2.0:
        return "Strong El Niño"
    elif anomaly >= 1.5:
        return "Moderate El Niño"
    elif anomaly >= 0.8:
        return "Weak El Niño"
    elif anomaly <= -2.0:
        return "Strong La Niña"
    elif anomaly <= -1.5:
        return "Moderate La Niña"
    elif anomaly <= -0.8:
        return "Weak La Niña"
    else:
        return "Neutral"

def main():
    print("Fetching NOAA ONI data …")
    records = fetch_oni()

    if not records:
        raise RuntimeError("No ONI records parsed — check NOAA URL or format.")

    latest = records[-1]
    output = {
        "updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": ONI_URL,
        "latest": {
            **latest,
            "label": el_nino_label(latest["anomaly"]),
        },
        "timeseries": records,
    }

    out_path = DATA_DIR / "nino34.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"✅  Saved {len(records)} ONI records → {out_path}")
    print(f"   Latest: {latest['season']} {latest['year']} → {latest['anomaly']:+.2f}°C ({el_nino_label(latest['anomaly'])})")

if __name__ == "__main__":
    main()
