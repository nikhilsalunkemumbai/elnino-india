"""
update_iod_soi.py
Fetches:
  - Indian Ocean Dipole (IOD / DMI) — primary: NOAA PSL ERSSTv5-derived DMI
                                       fallback: JAMSTEC long-run DMI series
  - Southern Oscillation Index (SOI) — NOAA CPC (permanent, unchanged)

Why the source change for IOD:
  BOM switched from Traditional to Relative Niño indices in Sep 2025 and their
  flat-file URLs (iod_1997.txt) are tied to internal site structure that has
  changed before. NOAA PSL computes DMI independently from ERSSTv5 SSTs and
  publishes a stable text file that has not changed format since the 1990s.
  JAMSTEC is kept as an explicit fallback since it has published DMI since 1997
  with a stable URL and is widely used in peer-reviewed research.

Outputs:
  data/iod.json
  data/soi.json
"""

import json
import re
import requests
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# --- IOD / DMI sources (in priority order) ---

# Primary: NOAA PSL ERSSTv5 Dipole Mode Index monthly text file
# Stable since 1990s, same format as their Nino3.4 files, not ENSO-hype-dependent
NOAA_DMI_URL = (
    "https://psl.noaa.gov/data/correlation/dmi.data"
)

# Fallback: JAMSTEC (Japan Agency for Marine-Earth Science and Technology)
# Has published weekly/monthly DMI since 1997; peer-reviewed, independent of BOM
JAMSTEC_DMI_URL = (
    "https://www.jamstec.go.jp/aplinfo/sintexf/iod/iod_index_ersstv5.txt"
)

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


def parse_noaa_dmi(text: str) -> list:
    """
    NOAA PSL dmi.data format:
      Header line(s) starting with non-digit characters, then:
      YYYY  v1  v2  v3  v4  v5  v6  v7  v8  v9  v10  v11  v12
      Missing = -9.99 or -99.99
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
            if val <= -9.0:   # missing sentinel
                continue
            records.append({
                "date": f"{year}-{month_idx:02d}-01",
                "year": year,
                "month": month_idx,
                "dmi": round(val, 3),
                "label": iod_label(val),
                "source": "NOAA PSL ERSSTv5",
            })
    return records


def parse_jamstec_dmi(text: str) -> list:
    """
    JAMSTEC format (weekly or monthly depending on file):
      YYYY/MM/DD  DMI
    or
      YYYY  MM  DMI
    Missing = 9999 or -9999
    """
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Try YYYY/MM/DD  DMI
        m = re.match(r"^(\d{4})/(\d{2})/(\d{2})\s+([-\d.]+)$", line)
        if m:
            year, month, _, dmi_str = m.groups()
        else:
            # Try YYYY  MM  DMI
            parts = line.split()
            if len(parts) < 3:
                continue
            year, month, dmi_str = parts[0], parts[1], parts[2]
        try:
            dmi = float(dmi_str)
        except ValueError:
            continue
        if abs(dmi) > 9000:
            continue
        records.append({
            "date": f"{year}-{int(month):02d}-01",
            "year": int(year),
            "month": int(month),
            "dmi": round(dmi, 3),
            "label": iod_label(dmi),
            "source": "JAMSTEC ERSSTv5",
        })
    return records


def fetch_iod() -> list:
    """Try NOAA PSL first, fall back to JAMSTEC."""
    for url, parser, name in [
        (NOAA_DMI_URL,    parse_noaa_dmi,    "NOAA PSL"),
        (JAMSTEC_DMI_URL, parse_jamstec_dmi, "JAMSTEC"),
    ]:
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            records = parser(resp.text)
            if records:
                print(f"   IOD source: {name} ({url})")
                return records
            print(f"   {name} returned 0 records — trying fallback …")
        except Exception as exc:
            print(f"   {name} failed ({exc}) — trying fallback …")
    raise RuntimeError("All IOD sources failed. Check network or source URLs.")


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
                        "date": f"{current_year}-{i+1:02d}-01",
                        "year": current_year,
                        "month": i + 1,
                        "soi": soi_val,
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
        "sources":    [NOAA_DMI_URL, JAMSTEC_DMI_URL],
        "note":       "Primary: NOAA PSL ERSSTv5 DMI. Fallback: JAMSTEC ERSSTv5 DMI.",
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
