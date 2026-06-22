"""
update_iod_soi.py
Fetches:
  - Indian Ocean Dipole (IOD / DMI) from BOM
  - Southern Oscillation Index (SOI) from NOAA CPC

Outputs:
  data/iod.json
  data/soi.json
"""

import json
import re
import requests
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# BOM IOD weekly DMI text file
IOD_URL = "http://www.bom.gov.au/climate/enso/iod_1997.txt"

# NOAA CPC SOI monthly
SOI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/soi"


def iod_label(dmi: float) -> str:
    if dmi >= 0.4:
        return "Positive IOD"
    elif dmi <= -0.4:
        return "Negative IOD"
    return "Neutral IOD"


def fetch_iod():
    resp = requests.get(IOD_URL, timeout=30)
    resp.raise_for_status()
    records = []
    for line in resp.text.splitlines():
        parts = line.split()
        # BOM format: YYYY  WW  DMI
        if len(parts) < 3:
            continue
        year, week, dmi = parts[0], parts[1], parts[2]
        if not re.match(r"^-?\d+\.\d+$", dmi):
            continue
        records.append({
            "year": int(year),
            "week": int(week),
            "dmi": float(dmi),
            "label": iod_label(float(dmi)),
        })
    return records


def soi_label(soi: float) -> str:
    if soi <= -7:
        return "El Niño signal"
    elif soi >= 7:
        return "La Niña signal"
    return "Neutral"


def fetch_soi():
    resp = requests.get(SOI_URL, timeout=30)
    resp.raise_for_status()
    records = []
    current_year = None
    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
              "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
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
                        "date": f"{current_year}-{i+1:02d}-01",
                        "year": current_year,
                        "month": i + 1,
                        "soi": soi_val,
                        "label": soi_label(soi_val),
                    })
                except ValueError:
                    pass
    return records


def main():
    print("Fetching IOD (BOM) …")
    iod_records = fetch_iod()
    latest_iod = iod_records[-1] if iod_records else {}
    iod_output = {
        "updated": datetime.utcnow().isoformat() + "Z",
        "source": IOD_URL,
        "latest": latest_iod,
        "timeseries": iod_records,
    }
    iod_path = DATA_DIR / "iod.json"
    iod_path.write_text(json.dumps(iod_output, indent=2))
    print(f"✅  Saved {len(iod_records)} IOD records → {iod_path}")
    if latest_iod:
        print(f"   Latest DMI: {latest_iod.get('dmi', 'N/A'):+.2f} ({latest_iod.get('label', '')})")

    print("Fetching SOI (NOAA CPC) …")
    soi_records = fetch_soi()
    latest_soi = soi_records[-1] if soi_records else {}
    soi_output = {
        "updated": datetime.utcnow().isoformat() + "Z",
        "source": SOI_URL,
        "latest": latest_soi,
        "timeseries": soi_records,
    }
    soi_path = DATA_DIR / "soi.json"
    soi_path.write_text(json.dumps(soi_output, indent=2))
    print(f"✅  Saved {len(soi_records)} SOI records → {soi_path}")
    if latest_soi:
        print(f"   Latest SOI: {latest_soi.get('soi', 'N/A'):+.1f} ({latest_soi.get('label', '')})")


if __name__ == "__main__":
    main()
