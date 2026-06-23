---
title: About
---

# About This Dashboard

The **India El Niño Intelligence Dashboard** is a free, open-source climate monitoring tool focused on the impacts of El Niño on India's monsoon, agriculture, and water supply.

## Why This Dashboard Exists

Global climate monitors like NOAA report ENSO status in Pacific-centric terms. This dashboard translates those signals into **India-specific risk indicators** — rainfall deficits, reservoir stress, and drought likelihood — giving journalists, policymakers, and farming communities actionable intelligence.

## Indicators Explained

| Indicator | What it measures | El Niño impact on India |
|-----------|-----------------|------------------------|
| **Niño3.4** | Sea surface temperature anomaly in central Pacific | +0.8°C → El Niño conditions; tends to weaken Indian monsoon |
| **SOI** | Pressure difference (Tahiti − Darwin) | Sustained < −7 supports El Niño |
| **IOD** | Indian Ocean SST east–west gradient | Positive IOD can *offset* El Niño drought risk |
| **Monsoon Rainfall** | Deviation from long-period average | Direct drought/flood indicator |
| **Reservoir Storage** | Live storage % of capacity (via CWC RSMS link) | Water stress indicator for drought planning |
| **Stress Index** | Composite 0–100 risk score | Summary indicator for decision-making |

## Methodology

The **Monsoon Stress Index** is a weighted composite of five climate and water indicators:

| Component | Weight |
|-----------|--------|
| Niño3.4 anomaly | 30% |
| SOI | 20% |
| IOD (DMI) | 20% |
| Rainfall deficit | 20% |
| Reservoir storage | 10% |

Scores: **0–24** Low · **25–49** Moderate · **50–74** High · **75–100** Severe

> **Note:** The Monsoon Stress Index is an experimental composite indicator, not a peer-reviewed forecast product. Weights reflect the relative importance of each driver in historical India monsoon literature but have not been formally backtested. Use alongside IMD seasonal forecasts and official advisories.

## Data Sources

Data is fetched daily by GitHub Actions from permanent, public-domain sources. No API keys or registration are required for NOAA, CHIRPS, or JAMSTEC sources.

| Indicator | Primary source | Fallback |
|-----------|---------------|---------| 
| Niño3.4 / ONI | NOAA/CPC — https://www.cpc.ncep.noaa.gov/ | NOAA/PSL ERSSTv5 |
| SOI | NOAA/CPC — https://www.cpc.ncep.noaa.gov/ | — |
| IOD / DMI | NOAA PSL ERSSTv5 — https://psl.noaa.gov/ | JAMSTEC — https://www.jamstec.go.jp/ |
| Rainfall anomalies | CHIRPS v3 (CHC/UCSB) — https://chc.ucsb.edu/data/chirps3 | NOAA PSL CPC Unified Gauge |
<<<<<<< HEAD
| Reservoir storage | CWC RSMS public dashboard — https://rsms.cwc.gov.in/frameWork/web/public-dashboard | Linked directly; not fetched by ETL |
=======
| Reservoir storage | CWC RSMS bulletin PDF — https://rsms.cwc.gov.in/ | Stale git data (last known values) |
>>>>>>> 3417b3277c343fca9e1146920c442d9d531aeecd

> **Note on IOD source:** BOM Australia switched to a revised index methodology in September 2025. This dashboard sources the IOD/DMI independently from NOAA PSL ERSSTv5 data, with JAMSTEC as fallback, ensuring continuity regardless of BOM's internal changes. IOD values from this dashboard may differ slightly from BOM's published figures.

> **Note on CHIRPS:** This dashboard uses CHIRPS v3, which became operational in January 2025 and replaces v2 (retiring December 2026).

<<<<<<< HEAD
> **Note on reservoir data:** Live reservoir storage is provided directly via the [CWC RSMS public dashboard](https://rsms.cwc.gov.in/frameWork/web/public-dashboard), which is maintained by the Central Water Commission and updated every Thursday. The Monsoon Stress Index uses a neutral 60% placeholder for the reservoir component (10% weight) rather than attempting to fetch and parse the bulletin programmatically.
=======
> **Note on reservoir data:** The CWC RSMS bulletin is published weekly (Thursdays). Fetching it from GitHub Actions requires a session cookie (`RSMS_SESSION_COOKIE` secret). Without the cookie, the dashboard displays a data-unavailable notice for the reservoir section and uses a neutral fallback in the stress index. See the README for setup instructions.
>>>>>>> 3417b3277c343fca9e1146920c442d9d531aeecd

## Technology

Built with [Observable Framework](https://observablehq.com/framework/), Python ETL scripts, GitHub Actions for daily automation, and GitHub Pages for free static hosting. All dependencies are open-source.

**Python dependencies:** `requests`, `numpy`, `pandas`, `pdfplumber`, `beautifulsoup4`

All code is open-source under the **MIT License**.

## Contributing

Pull requests are welcome! See the [GitHub repository](https://github.com/YOUR_USERNAME/elnino-india) for contribution guidelines.
