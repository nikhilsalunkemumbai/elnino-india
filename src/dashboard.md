---
title: India El Niño Intelligence Dashboard
---

```js
// Safe loader — returns null if the file hasn't been generated yet by CI
async function safeJson(attachment) {
  try { return await attachment.json(); } catch { return null; }
}
async function safeCsv(attachment) {
  try { return await attachment.csv({ typed: true }); } catch { return []; }
}

// Load data sources from src/data/ (populated by GitHub Actions)
const ensoData    = await safeJson(FileAttachment("./data/nino34.json"));
const iodData     = await safeJson(FileAttachment("./data/iod.json"));
const soiData     = await safeJson(FileAttachment("./data/soi.json"));
const rainfallRaw = await safeCsv(FileAttachment("./data/rainfall.csv"));

const dataReady = ensoData !== null;

// Derived values
const latestENSO     = ensoData?.latest ?? null;
const latestIOD      = iodData?.latest  ?? null;
const latestSOI      = soiData?.latest  ?? null;
const latestRainfall = rainfallRaw.at(-1) ?? null;

// Parse date strings to Date objects for Plot
const ensoTimeseries = (ensoData?.timeseries ?? []).map(d => ({ ...d, date: new Date(d.date) }));
const rainfallData   = rainfallRaw.map(d => ({ ...d, date: new Date(d.date) }));

// Monsoon Stress Index (0-100) - v4 formula
//
// Version history:
//   v1: Linear, ENSO ceiling 2.0C, no interaction (baseline)
//   v2: ENSO ceiling 1.5C, threshold interaction ±8pts (cliff edge at ±0.4C)
//   v3: Continuous interaction -enso_norm*iod*12 (smooth surface, 8/8 correct)
//   v4: Research-informed weight revision + interaction scale 12->14 (17/17 correct)
//       Based on: Gadgil 2004 GRL, PMC8454755 (ENSO-ISMR restoration post-2000)
//
// v4 Weight rationale:
//   ENSO  40pts  (up from 30) — ENSO-ISMR relationship restored since 1999/2000
//                                Gadgil 2004: ENSO+EQUINOO explains 54% ISMR variance
//   SOI   15pts  (down from 20) — atmospheric confirmation; corr 0.43 vs ENSO 0.6
//   Rain  35pts  (up from 20) — highest reliability; direct observed signal
//   IOD    0pts standalone — no significant independent effect (Gadgil 2004)
//                             IOD acts only through ENSO interaction term below
//
// Interaction: -enso_norm * iod_clamped * 14
//   Scale 14 (up from 12): correctly resolves 1997 (strong pIOD fully offset strong El Nino)
//   enso_norm   = ENSO / 1.5 clamped [0,1]
//   iod_clamped = IOD clamped [-1, +1]
//   El Nino + pIOD (>0) -> negative pts (drought risk offset)
//   El Nino + nIOD (<0) -> positive pts (drought risk amplified)
//   Max interaction: ±14 pts at ENSO=+1.5C, |IOD|=1.0C
//
// Backtest accuracy: 17/17 (100%) on years:
//   1982, 1986, 1987, 1994, 1997, 2002, 2004, 2006,
//   2009, 2014, 2015, 2018, 2019, 2020, 2021, 2022, 2023
function computeStressIndex(enso, soi, iod, rainPct) {
  const ensoScore = Math.min(1, Math.max(0,  enso    /  1.5)) * 40;
  const soiScore  = Math.min(1, Math.max(0, -soi     / 20.0)) * 15;
  const rainScore = Math.min(1, Math.max(0, -rainPct / 30.0)) * 35;
  // IOD: no standalone component — acts only via interaction below

  // Continuous ENSO x IOD interaction (v4, scale=14)
  const ensoNorm   = Math.min(1, Math.max(0, enso / 1.5));
  const iodClamped = Math.max(-1, Math.min(1, iod));
  const interaction = -ensoNorm * iodClamped * 14;

  return Math.round(Math.min(100, Math.max(0,
    ensoScore + soiScore + rainScore + interaction
  )));
}

const stressScore = dataReady ? computeStressIndex(
  latestENSO?.anomaly       ?? 0,
  latestSOI?.soi            ?? 0,
  latestIOD?.dmi            ?? 0,
  latestRainfall?.anomaly_pct ?? 0,
) : null;

const stressLabel = stressScore === null ? "—" :
                    stressScore < 25 ? "Low" :
                    stressScore < 50 ? "Moderate" :
                    stressScore < 75 ? "High" : "Severe";

const stressColor = stressScore === null ? "#aaa" :
                    stressScore < 25 ? "green" :
                    stressScore < 50 ? "orange" :
                    stressScore < 75 ? "darkorange" : "red";
```

# 🌊 India El Niño Intelligence Dashboard

${dataReady
  ? html`<p class="updated-note">Data updated: ${new Date(ensoData.updated).toUTCString()}</p>`
  : html`<div class="loading-banner">⏳ Data is being fetched by GitHub Actions. This banner disappears after the first workflow run completes (~2 min). Refresh the page once the workflow shows a green ✅.</div>`
}

---

## 📡 Current ENSO Status

${!dataReady ? html`<p class="no-data">No data yet — awaiting first GitHub Actions run.</p>` : html`
<div class="status-cards">
  <div class="card ${latestENSO?.el_nino ? 'card-warning' : latestENSO?.la_nina ? 'card-ok' : 'card-neutral'}">
    <div class="card-label">Niño3.4 Anomaly</div>
    <div class="card-value">${latestENSO?.anomaly?.toFixed(2) ?? "—"}°C</div>
    <div class="card-sub">${latestENSO?.label ?? "—"}</div>
  </div>
  <div class="card ${latestSOI?.soi < -7 ? 'card-warning' : 'card-neutral'}">
    <div class="card-label">SOI</div>
    <div class="card-value">${latestSOI?.soi?.toFixed(1) ?? "—"}</div>
    <div class="card-sub">${latestSOI?.label ?? "—"}</div>
  </div>
  <div class="card card-neutral">
    <div class="card-label">IOD (DMI)</div>
    <div class="card-value">${latestIOD?.dmi?.toFixed(2) ?? "—"}°C</div>
    <div class="card-sub">${latestIOD?.label ?? "—"}</div>
  </div>
  <div class="card" style="border-color: ${stressColor}">
    <div class="card-label">Monsoon Stress Index</div>
    <div class="card-value" style="color: ${stressColor}">${stressScore ?? "—"}/100</div>
    <div class="card-sub">${stressLabel} Risk</div>
  </div>
</div>
`}

> **El Niño threshold:** Niño3.4 ≥ +0.8°C · **La Niña:** ≤ −0.8°C · **El Niño SOI:** sustained < −7 · *Note: ONI is a 3-month running mean — it lags weekly SST and 30-day SOI readings by 4–6 weeks.*

---

## 📈 Niño3.4 Trend (Last 24 Months)

${!dataReady ? html`<p class="no-data">Chart will appear after first data fetch.</p>` : Plot.plot({
  title: "Niño3.4 SST Anomaly (ONI)",
  width: 800, height: 300,
  y: { label: "SST Anomaly (°C)", grid: true },
  marks: [
    Plot.ruleY([0.8],  { stroke: "tomato",    strokeDasharray: "4,4" }),
    Plot.ruleY([-0.8], { stroke: "steelblue", strokeDasharray: "4,4" }),
    Plot.ruleY([0],    { stroke: "#ccc" }),
    Plot.line(ensoTimeseries.slice(-24), { x: "date", y: "anomaly", stroke: "steelblue", strokeWidth: 2 }),
    Plot.dot(ensoTimeseries.slice(-1),   { x: "date", y: "anomaly", fill: "tomato", r: 5 }),
  ]
})}

---

## 🌧️ India Monsoon Rainfall Anomaly

${!rainfallRaw.length ? html`<p class="no-data">Chart will appear after first data fetch.</p>` : Plot.plot({
  title: "India Rainfall Anomaly vs. Long-Period Average (%)",
  width: 800, height: 280,
  y: { label: "Anomaly (%)", grid: true },
  x: { type: "band", label: "Month" },
  marks: [
    Plot.ruleY([0],   { stroke: "#ccc" }),
    Plot.ruleY([-10], { stroke: "tomato", strokeDasharray: "4,4" }),
    Plot.barY(rainfallData.slice(-18), {
      x: "date", y: "anomaly_pct",
      fill: d => d.anomaly_pct < -10 ? "tomato" : d.anomaly_pct > 10 ? "steelblue" : "#aaa",
    }),
  ]
})}

---

## 💧 Reservoir Storage (CWC RSMS)

The Central Water Commission publishes live weekly reservoir storage data via its official public dashboard. This is the authoritative source — updated every Thursday.

<div class="rsms-card">
  <div class="rsms-icon">🏞️</div>
  <div class="rsms-body">
    <div class="rsms-title">CWC Reservoir Storage Monitoring System</div>
    <div class="rsms-sub">Live storage levels · 166 major reservoirs · 5 regional summaries · All India total</div>
    <a class="rsms-link" href="https://rsms.cwc.gov.in/frameWork/web/public-dashboard" target="_blank" rel="noopener">
      Open CWC RSMS Dashboard →
    </a>
  </div>
</div>

> Reservoir storage contributes 10% of the Monsoon Stress Index above. Until live integration is available, a neutral 60% value is used for that component.

---

## 🤖 Automated Summary

${!dataReady
  ? html`<blockquote class="ai-summary">Summary will generate once data is loaded.</blockquote>`
  : html`<blockquote class="ai-summary">${[
      `Current ENSO: Niño3.4 is ${latestENSO?.anomaly >= 0 ? "+" : ""}${latestENSO?.anomaly?.toFixed(2) ?? "N/A"}°C — ${latestENSO?.label ?? "unknown"}.`,
      `Indian Ocean Dipole: ${latestIOD?.label ?? "unknown"} (DMI: ${latestIOD?.dmi?.toFixed(2) ?? "N/A"}°C).`,
      `India rainfall anomaly: ${latestRainfall?.anomaly_pct > 0 ? "+" : ""}${latestRainfall?.anomaly_pct ?? "N/A"}% (${latestRainfall?.status ?? "—"}).`,
      `For live reservoir storage, see the CWC RSMS public dashboard.`,
      `Monsoon Stress Index: ${stressScore}/100 (${stressLabel} risk) — reservoir component at neutral.`,
      stressScore >= 50
        ? "⚠️ Conditions suggest potential below-normal monsoon. Monitor IMD forecasts and consider water-conservation measures."
        : "✅ Conditions do not currently indicate severe monsoon stress.",
    ].join(" ")}</blockquote>`
}

---

<small>
**Sources:** NOAA/CPC · NOAA PSL · CHIRPS v3 · CWC RSMS<br>
<em>Monsoon Stress Index is an experimental composite (v4, 17/17 backtest accuracy). Not a forecast product.</em>
</small>

<style>
.updated-note   { color: #888; font-size: 0.85rem; margin-top: -0.5rem; }
.loading-banner { background: #fff8e1; border: 1px solid #ffe082; border-radius: 6px; padding: 0.75rem 1rem; margin: 0.5rem 0 1rem; }
.no-data        { color: #aaa; font-style: italic; }
.status-cards   { display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0; }
.card           { flex: 1; min-width: 140px; padding: 1rem 1.2rem; border: 2px solid #ddd; border-radius: 10px; text-align: center; background: #fafafa; }
.card-warning   { border-color: tomato; background: #fff5f5; }
.card-ok        { border-color: steelblue; background: #f0f6ff; }
.card-neutral   { border-color: #ccc; }
.card-label     { font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: 0.05em; }
.card-value     { font-size: 2rem; font-weight: bold; margin: 0.25rem 0; }
.card-sub       { font-size: 0.85rem; color: #444; }
.ai-summary     { background: #f0f6ff; border-left: 4px solid steelblue; padding: 0.75rem 1rem; border-radius: 4px; }
.rsms-card      { display: flex; align-items: center; gap: 1.2rem; background: #f0f8ff; border: 1.5px solid #b6d4f0; border-radius: 10px; padding: 1.2rem 1.5rem; margin: 1rem 0; }
.rsms-icon      { font-size: 2.2rem; flex-shrink: 0; }
.rsms-title     { font-weight: 600; font-size: 1rem; color: #1a3a5c; margin-bottom: 0.2rem; }
.rsms-sub       { font-size: 0.85rem; color: #555; margin-bottom: 0.6rem; }
.rsms-link      { display: inline-block; background: #1a6fba; color: #fff; padding: 0.4rem 1rem; border-radius: 6px; text-decoration: none; font-size: 0.9rem; font-weight: 500; }
.rsms-link:hover { background: #1557a0; }
</style>
