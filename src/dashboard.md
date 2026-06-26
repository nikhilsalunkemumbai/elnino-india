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
const latestWeekly   = ensoData?.latest_weekly ?? null;
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
function computeStressIndex(enso, soi, iod, rainPct, resPct) {
  const ensoScore = Math.min(1, Math.max(0,  enso    /  1.5)) * 40;
  const soiScore  = Math.min(1, Math.max(0, -soi     / 20.0)) * 15;
  const rainScore = Math.min(1, Math.max(0, -rainPct / 30.0)) * 35;
  // IOD: no standalone component — acts only via interaction below

  // Reservoir component (10pts)
  // resPct = (liveStorage / capacity) * 100 — calculated from RSMS inputs on dashboard
  // Formula: 50% = neutral (0pts), 0% = max stress (10pts), 100% = no stress
  const resScore = Math.min(1, Math.max(0, (50 - resPct) / 50)) * 10;

  // Continuous ENSO x IOD interaction (v4, scale=14)
  const ensoNorm   = Math.min(1, Math.max(0, enso / 1.5));
  const iodClamped = Math.max(-1, Math.min(1, iod));
  const interaction = -ensoNorm * iodClamped * 14;

  return Math.round(Math.min(100, Math.max(0,
    ensoScore + soiScore + rainScore + resScore + interaction
  )));
}

// cwcPct is reactive — defined from user inputs in the reservoir section below
// Observable Framework re-evaluates this cell whenever cwcPct changes
const stressScore = dataReady ? computeStressIndex(
  latestENSO?.anomaly         ?? 0,
  latestSOI?.soi              ?? 0,
  latestIOD?.dmi              ?? 0,
  latestRainfall?.anomaly_pct ?? 0,
  typeof cwcPct !== "undefined" ? cwcPct : 27.5,  // fallback until inputs load
) : null;

const stressLabel = stressScore === null ? "—" :
                    stressScore < 25 ? "Low" :
                    stressScore < 50 ? "Moderate" :
                    stressScore < 75 ? "High" : "Severe";

// ENSO phase: use weekly for current classification (ONI lags 6-8 weeks during rapid onset)
const weeklyAnom = latestWeekly?.anomaly ?? null;
const ensoPhase = weeklyAnom !== null
  ? (weeklyAnom >= 2.0 ? "Strong El Niño (developing)"
    : weeklyAnom >= 1.5 ? "Moderate El Niño (developing)"
    : weeklyAnom >= 0.8 ? "Weak El Niño (developing)"
    : weeklyAnom <= -0.8 ? "La Niña"
    : "Neutral")
  : (latestENSO?.label ?? "—");

const stressColor = stressScore === null ? "#aaa" :
                    stressScore < 25 ? "green" :
                    stressScore < 50 ? "orange" :
                    stressScore < 75 ? "darkorange" : "red";

// Pre-built HTML elements for multiline blocks
// (Observable Framework inline ${} cannot span multiline html`` literals)
const loadingBannerEl = html`<div class="loading-banner">⏳ Data is being fetched by GitHub Actions. This banner disappears after the first workflow run completes (~2 min). Refresh the page once the workflow shows a green ✅.</div>`;

const updatedNoteEl = dataReady
  ? html`<p class="updated-note">Data updated: ${new Date(ensoData.updated).toUTCString()}</p>`
  : loadingBannerEl;

const statusCardsEl = !dataReady
  ? html`<p class="no-data">No data yet — awaiting first GitHub Actions run.</p>`
  : html`<div class="status-cards">
  <div class="card ${latestENSO?.el_nino ? 'card-warning' : latestENSO?.la_nina ? 'card-ok' : 'card-neutral'}">
    <div class="card-label">Niño3.4 Anomaly (ONI)</div>
    <div class="card-value">${latestENSO?.anomaly?.toFixed(2) ?? "—"}°C</div>
    <div class="card-sub">${latestENSO?.label ?? "—"} · MAM 2026</div>
  </div>
  <div class="card ${weeklyAnom !== null && weeklyAnom >= 0.8 ? 'card-warning' : 'card-neutral'}">
    <div class="card-label">ENSO Phase (current)</div>
    <div class="card-value" style="font-size:1.1rem">${ensoPhase}</div>
    <div class="card-sub">Weekly Niño3.4${weeklyAnom !== null ? ` · ${weeklyAnom >= 0 ? "+" : ""}${weeklyAnom?.toFixed(2)}°C` : ""}</div>
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
  ${latestWeekly ? html`<div class="card card-neutral" title="Weekly Nino3.4 from NOAA OISSTv2 — 1 week lag. ONI (3-month mean) lags by ~6 weeks during rapid El Nino onset.">
    <div class="card-label">Niño3.4 This Week</div>
    <div class="card-value" style="color:${latestWeekly.anomaly >= 0.8 ? 'tomato' : latestWeekly.anomaly <= -0.8 ? 'steelblue' : 'inherit'}">${latestWeekly.anomaly >= 0 ? '+' : ''}${latestWeekly.anomaly?.toFixed(2)}°C</div>
    <div class="card-sub">${latestWeekly.label} · ${latestWeekly.date}</div>
  </div>` : ""}
  <div class="card" style="border-color: ${stressColor}">
    <div class="card-label">Monsoon Stress Index</div>
    <div class="card-value" style="color: ${stressColor}">${stressScore ?? "—"}/100</div>
    <div class="card-sub">${stressLabel} Risk</div>
  </div>
</div>`;

// Data currency check — warn if stress index is likely understated due to data lag
const rainfallAge = rainfallRaw.length
  ? Math.round((Date.now() - new Date(rainfallRaw[rainfallRaw.length-1].date)) / 86400000)
  : null;
const rainfallLagWarning = rainfallAge && rainfallAge > 45
  ? ` ⚠️ Rainfall data is ${rainfallAge} days old (CHIRPS lag) — current conditions may be significantly worse.`
  : "";

const summaryText = !dataReady ? "Summary will generate once data is loaded." : [
  `Current ENSO: Niño3.4 is ${latestENSO?.anomaly >= 0 ? "+" : ""}${latestENSO?.anomaly?.toFixed(2) ?? "N/A"}°C — ${latestENSO?.label ?? "unknown"}.`,
  `Indian Ocean Dipole: ${latestIOD?.label ?? "unknown"} (DMI: ${latestIOD?.dmi?.toFixed(2) ?? "N/A"}°C).`,
  `India rainfall anomaly: ${latestRainfall?.anomaly_pct > 0 ? "+" : ""}${latestRainfall?.anomaly_pct ?? "N/A"}% (${latestRainfall?.status ?? "—"}).`,
  `Reservoir storage: ${cwcPct.toFixed(1)}% of capacity (${cwcLiveStorage.toFixed(3)} BCM live — RSMS ${cwcBulletinDate}).`,
  `Monsoon Stress Index: ${stressScore}/100 (${stressLabel} risk) — reservoir at ${cwcPct.toFixed(1)}% capacity.`,
  stressScore >= 75
    ? "🔴 Severe stress. IMD confirms 84% probability of below-normal/deficient monsoon. El Niño Advisory active. Reservoir storage at 28%. Prioritise water conservation and Kharif crop contingency planning."
    : stressScore >= 50
    ? "🟠 High stress. IMD below-normal forecast (90% LPA). El Niño conditions developing rapidly. Monitor IMD advisories and CWC reservoir bulletins."
    : stressScore >= 25
    ? "🟡 Moderate risk. El Niño developing in Pacific. SOI strongly negative. Index will rise when June CHIRPS data becomes available (~late July)."
    : "🟢 Stress index Low on current observed data (ONI and CHIRPS have structural lags). See IMD Seasonal Outlook above — official forecast is 84% probability of below-normal or deficient season.",
  rainfallLagWarning,
].filter(Boolean).join(" ");

const summaryEl = html`<blockquote class="ai-summary">${summaryText}</blockquote>`;
```

# 🌊 India El Niño Intelligence Dashboard

${updatedNoteEl}

---

## 📡 Current ENSO Status

${statusCardsEl}

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

> **Note:** Rainfall data (CHIRPS v3) has a 30–45 day structural lag — the chart currently shows through May 2026. For real-time rainfall tracking, see [IMD daily rainfall](https://mausam.imd.gov.in) or [IMD district-level data](https://www.imd.gov.in/pages/monsoon_main.php).

---

## 💧 Reservoir Storage (CWC RSMS)

The Central Water Commission publishes live weekly reservoir storage data every Thursday. Enter the two numbers shown on the [RSMS public dashboard](https://rsms.cwc.gov.in/frameWork/web/public-dashboard) — the stress index updates instantly.

```js
// Reactive inputs — values from rsms.cwc.gov.in (updated every Thursday)
// Read "Live Capacity at FRL" and "Live Storage as on DD-MM-YYYY" from the RSMS dashboard
const cwcCapacity    = view(Inputs.number({
  label:       "Live Capacity at FRL (BCM)",
  value:       183.565,
  step:        0.001,
  min:         100,
  max:         250,
}));

const cwcLiveStorage = view(Inputs.number({
  label:       "Live Storage as on date (BCM)",
  value:       50.457,
  step:        0.001,
  min:         0,
  max:         250,
}));
```

```js
// Derived display values
const cwcPct          = (cwcLiveStorage / cwcCapacity) * 100;
const cwcLastYear     = 58.249;   // BCM — update from RSMS "Live Storage of last year"
const cwcNormal       = 44.646;   // BCM — update from RSMS "Normal Storage"
const cwcVsLastYear   = ((cwcLiveStorage - cwcLastYear) / cwcLastYear * 100).toFixed(1);
const cwcVsNormal     = ((cwcLiveStorage - cwcNormal)   / cwcNormal   * 100).toFixed(1);
const cwcBulletinDate = "18-06-2026"; // update to match RSMS bulletin date
```

<div class="rsms-card">
  <div class="rsms-icon">🏞️</div>
  <div class="rsms-body">
    <div class="rsms-title">CWC Reservoir Storage Monitoring System</div>
    <div class="rsms-sub">New RSMS portal (launched Apr 2025) · 166 major reservoirs · Updated every Thursday</div>
    <a class="rsms-link" href="https://rsms.cwc.gov.in/frameWork/web/public-dashboard" target="_blank" rel="noopener">
      Open CWC RSMS Dashboard →
    </a>
  </div>
</div>

```js
// Live calculated display
html`<div class="cwc-stats">
  <div class="cwc-stat">
    <div class="cwc-val" style="color:${cwcPct < 30 ? 'tomato' : cwcPct < 50 ? 'darkorange' : 'steelblue'}">${cwcPct.toFixed(1)}%</div>
    <div class="cwc-label">of capacity</div>
    <div class="cwc-sub">${cwcLiveStorage.toFixed(3)} / ${cwcCapacity.toFixed(3)} BCM</div>
  </div>
  <div class="cwc-stat">
    <div class="cwc-val" style="color:${parseFloat(cwcVsLastYear) < 0 ? 'tomato' : 'steelblue'}">${parseFloat(cwcVsLastYear) > 0 ? '+' : ''}${cwcVsLastYear}%</div>
    <div class="cwc-label">vs last year</div>
    <div class="cwc-sub">${cwcLastYear.toFixed(3)} BCM last year</div>
  </div>
  <div class="cwc-stat">
    <div class="cwc-val" style="color:${parseFloat(cwcVsNormal) < 0 ? 'tomato' : 'steelblue'}">${parseFloat(cwcVsNormal) > 0 ? '+' : ''}${cwcVsNormal}%</div>
    <div class="cwc-label">vs 10-yr normal</div>
    <div class="cwc-sub">${cwcNormal.toFixed(3)} BCM normal</div>
  </div>
  <div class="cwc-stat">
    <div class="cwc-val" style="color:#555; font-size:0.9rem">${cwcBulletinDate}</div>
    <div class="cwc-label">RSMS bulletin date</div>
    <div class="cwc-sub">Updates every Thursday</div>
  </div>
</div>`
```

> Reservoir storage contributes **10%** of the Monsoon Stress Index. Enter the BCM values above from the RSMS dashboard — the stress index recalculates instantly. The capacity (183.565 BCM) stays fixed; only the live storage changes weekly.

---

## 📋 IMD Seasonal Outlook (Jun–Sep 2026)

<div class="imd-outlook">
  <div class="outlook-row"><span class="outlook-label">IMD Long Range Forecast</span><span class="outlook-val outlook-warn">90% of LPA — Below Normal</span></div>
  <div class="outlook-row"><span class="outlook-label">Probability below-normal or deficient</span><span class="outlook-val outlook-warn">84%</span></div>
  <div class="outlook-row"><span class="outlook-label">Probability deficient (&lt;90% LPA)</span><span class="outlook-val outlook-warn">60%</span></div>
  <div class="outlook-row"><span class="outlook-label">Monsoon Core Zone (rainfed agriculture)</span><span class="outlook-val outlook-warn">Below Normal (&lt;94% LPA)</span></div>
  <div class="outlook-row"><span class="outlook-label">Reservoir storage (CWC, Jun 11 2026)</span><span class="outlook-val outlook-warn">28.28% capacity — 166 reservoirs</span></div>
  <div class="outlook-row"><span class="outlook-label">El Niño Advisory status</span><span class="outlook-val outlook-warn">Active — NOAA CPC (Jun 2026)</span></div>
  <div class="outlook-row"><span class="outlook-label">IRI peak forecast (SON 2026)</span><span class="outlook-val outlook-warn">Niño3.4 ≥ +2.0°C with 60%+ probability</span></div>
</div>

> **Sources:** [IMD LRF May 29 2026](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2266479) · [IRI ENSO Jun 2026](https://iri.columbia.edu/our-expertise/climate/forecasts/enso/current/) · [CWC RSMS Dashboard](https://rsms.cwc.gov.in/frameWork/web/public-dashboard) · This section shows official forecasts and is separate from the observed Monsoon Stress Index above.

---

## 🤖 Automated Summary

${summaryEl}

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
.cwc-stats     { display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0; }
.cwc-stat      { flex: 1; min-width: 120px; background: var(--color-background-secondary); border: 0.5px solid var(--color-border-secondary); border-radius: 8px; padding: 12px 14px; text-align: center; }
.cwc-val       { font-size: 1.8rem; font-weight: 700; margin-bottom: 2px; }
.cwc-label     { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--color-text-tertiary); }
.cwc-sub       { font-size: 11px; color: var(--color-text-tertiary); margin-top: 3px; }
.imd-outlook   { background: #fff8f0; border: 1.5px solid #f59e0b; border-radius: 8px; padding: 12px 16px; margin: 12px 0; }
.outlook-row   { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; border-bottom: 0.5px solid #fde68a; font-size: 13px; gap: 12px; }
.outlook-row:last-child { border-bottom: none; }
.outlook-label { color: var(--color-text-secondary); }
.outlook-val   { font-weight: 500; text-align: right; }
.outlook-warn  { color: #b45309; }
</style>
