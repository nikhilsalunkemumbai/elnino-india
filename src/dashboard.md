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

// Load all data sources (populated by GitHub Actions; empty on first push)
const ensoData    = await safeJson(FileAttachment("../data/nino34.json"));
const iodData     = await safeJson(FileAttachment("../data/iod.json"));
const soiData     = await safeJson(FileAttachment("../data/soi.json"));
const rainfallRaw = await safeCsv(FileAttachment("../data/rainfall.csv"));
const reservoirs  = await safeCsv(FileAttachment("../data/reservoirs.csv"));

const dataReady = ensoData !== null;

// Derived values
const latestENSO     = ensoData?.latest ?? null;
const latestIOD      = iodData?.latest  ?? null;
const latestSOI      = soiData?.latest  ?? null;
const latestRainfall = rainfallRaw.at(-1) ?? null;

// Monsoon Stress Index (0–100)
function computeStressIndex(enso, soi, iod, rainPct, reservoirPct) {
  const ensoScore  = Math.min(100, Math.max(0, (enso  /  2.0) * 100 * 0.30));
  const soiScore   = Math.min(100, Math.max(0, (-soi  / 20.0) * 100 * 0.20));
  const iodScore   = Math.min(100, Math.max(0, (-iod  /  1.0) * 100 * 0.20));
  const rainScore  = Math.min(100, Math.max(0, (-rainPct / 30) * 100 * 0.20));
  const resScore   = Math.min(100, Math.max(0, ((50 - reservoirPct) / 50) * 100 * 0.10));
  return Math.round(ensoScore + soiScore + iodScore + rainScore + resScore);
}

const avgReservoirPct = reservoirs.length ? d3.mean(reservoirs, d => d.live_storage_pct) : null;
const stressScore = dataReady ? computeStressIndex(
  latestENSO?.anomaly  ?? 0,
  latestSOI?.soi        ?? 0,
  latestIOD?.dmi        ?? 0,
  latestRainfall?.anomaly_pct ?? 0,
  avgReservoirPct ?? 60,
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

> **El Niño threshold:** Niño3.4 ≥ +0.8°C · **La Niña:** ≤ −0.8°C · **El Niño SOI:** sustained < −7

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
    Plot.line(ensoData.timeseries.slice(-24), { x: "date", y: "anomaly", stroke: "steelblue", strokeWidth: 2 }),
    Plot.dot(ensoData.timeseries.slice(-1),   { x: "date", y: "anomaly", fill: "tomato", r: 5 }),
  ]
})}

---

## 🌧️ India Monsoon Rainfall Anomaly

${!rainfallRaw.length ? html`<p class="no-data">Chart will appear after first data fetch.</p>` : Plot.plot({
  title: "India Rainfall Anomaly vs. Long-Period Average (%)",
  width: 800, height: 280,
  y: { label: "Anomaly (%)", grid: true },
  marks: [
    Plot.ruleY([0],   { stroke: "#ccc" }),
    Plot.ruleY([-10], { stroke: "tomato", strokeDasharray: "4,4" }),
    Plot.bar(rainfallRaw.slice(-18), {
      x: "date", y: "anomaly_pct",
      fill: d => d.anomaly_pct < -10 ? "tomato" : d.anomaly_pct > 10 ? "steelblue" : "#aaa",
    }),
  ]
})}

---

## 💧 Major Reservoir Storage

```js
const reservoirIsStale = !reservoirs.length ||
  reservoirs.every(d => d.live_storage_pct === 0) ||
  reservoirs.some(d => String(d.source ?? "").includes("STALE") || String(d.source ?? "").includes("Stub"));

const reservoirDate = reservoirs.length ? reservoirs[0].date : null;
```

${reservoirIsStale
  ? html`<div class="stale-banner">⚠️ <strong>Reservoir data unavailable.</strong> The CWC RSMS live source could not be reached by the automated workflow. ${reservoirDate ? `Last attempted: ${reservoirDate}.` : ""} To enable live data, add the <code>RSMS_SESSION_COOKIE</code> secret — see the <a href="https://github.com/YOUR_USERNAME/elnino-india#-reservoir-data--optional-secret">README</a> for instructions. Stress index is using a neutral reservoir value.</div>`
  : Plot.plot({
      title: "Live Storage vs. 10-Year Average (% of Capacity)",
      width: 800, height: 350, marginLeft: 200,
      x: { label: "% of Capacity", domain: [0, 100] },
      marks: [
        Plot.barX(reservoirs, {
          x: "live_storage_pct", y: "name",
          fill: d => d.live_storage_pct < 30 ? "crimson" : d.live_storage_pct < 50 ? "darkorange" : "steelblue",
          sort: { y: "-x" },
        }),
        Plot.tickX(reservoirs, { x: "ten_yr_avg_pct", y: "name", stroke: "black", strokeWidth: 2 }),
      ]
  })
}

${!reservoirIsStale ? html`<p><small>Vertical tick = 10-year average. Red/orange bars are critically below normal.</small></p>` : ""}

---

## 🤖 Automated Summary

${!dataReady
  ? html`<blockquote class="ai-summary">Summary will generate once data is loaded.</blockquote>`
  : html`<blockquote class="ai-summary">${[
      `Current ENSO: Niño3.4 is ${latestENSO?.anomaly >= 0 ? "+" : ""}${latestENSO?.anomaly?.toFixed(2) ?? "N/A"}°C — ${latestENSO?.label ?? "unknown"}.`,
      `Indian Ocean Dipole: ${latestIOD?.label ?? "unknown"} (DMI: ${latestIOD?.dmi?.toFixed(2) ?? "N/A"}°C).`,
      `India rainfall anomaly: ${latestRainfall?.anomaly_pct > 0 ? "+" : ""}${latestRainfall?.anomaly_pct ?? "N/A"}% (${latestRainfall?.status ?? "—"}).`,
      `Reservoir storage: ${avgReservoirPct?.toFixed(1) ?? "—"}% of capacity across monitored basins.`,
      `Monsoon Stress Index: ${stressScore}/100 (${stressLabel} risk).`,
      stressScore >= 50
        ? "⚠️ Conditions suggest potential below-normal monsoon. Monitor IMD forecasts and consider water-conservation measures."
        : "✅ Conditions do not currently indicate severe monsoon stress.",
    ].join(" ")}</blockquote>`
}

---

<small>
**Sources:** NOAA/CPC · NOAA PSL · CHIRPS v3 · CWC RSMS · No personal data collected — see <a href="./privacy">Privacy Policy</a><br>
<em>Monsoon Stress Index is an experimental composite indicator, not a forecast product.</em>
</small>

<style>
.updated-note  { color: #888; font-size: 0.85rem; margin-top: -0.5rem; }
.loading-banner { background: #fff8e1; border: 1px solid #ffe082; border-radius: 6px; padding: 0.75rem 1rem; margin: 0.5rem 0 1rem; }
.no-data       { color: #aaa; font-style: italic; }
.status-cards  { display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0; }
.card { flex: 1; min-width: 140px; padding: 1rem 1.2rem; border: 2px solid #ddd; border-radius: 10px; text-align: center; background: #fafafa; }
.card-warning  { border-color: tomato; background: #fff5f5; }
.card-ok       { border-color: steelblue; background: #f0f6ff; }
.card-neutral  { border-color: #ccc; }
.card-label    { font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: 0.05em; }
.card-value    { font-size: 2rem; font-weight: bold; margin: 0.25rem 0; }
.card-sub      { font-size: 0.85rem; color: #444; }
.ai-summary    { background: #f0f6ff; border-left: 4px solid steelblue; padding: 0.75rem 1rem; border-radius: 4px; }
.stale-banner  { background: #fff8e1; border: 1px solid #ffe082; border-left: 4px solid #f59e0b; border-radius: 6px; padding: 0.75rem 1rem; margin: 0.5rem 0 1rem; color: #78350f; font-size: 0.9rem; }
</style>
