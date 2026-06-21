/**
 * monsoon_map.js
 * Renders a choropleth map of India coloured by rainfall anomaly or reservoir stress.
 *
 * Requires: India states GeoJSON (public domain from Datameet)
 *   Fetch at build time from:
 *   https://raw.githubusercontent.com/datameet/maps/master/Districts/India_Districts.geojson
 *   or the simpler states file:
 *   https://raw.githubusercontent.com/geohacker/india/master/state/india_state.geojson
 *
 * Usage in Observable Markdown:
 *   import { indiaMap } from "./components/monsoon_map.js";
 *   const geo = await fetch("https://raw.githubusercontent.com/geohacker/india/master/state/india_state.geojson").then(r => r.json());
 *   indiaMap(geo, rainfallByState)
 */

import * as d3 from "npm:d3";
import * as Plot from "npm:@observablehq/plot";

/**
 * @param {object} geojson         - India states GeoJSON FeatureCollection
 * @param {Map}    anomalyByState  - Map<stateName, anomalyPct>
 * @param {object} opts
 */
export function indiaMap(geojson, anomalyByState, { width = 500 } = {}) {
  const colorScale = d3.scaleSequential()
    .domain([-30, 30])
    .interpolator(d3.interpolateRdBu)
    .clamp(true);

  return Plot.plot({
    width,
    projection: { type: "mercator", domain: geojson },
    color: {
      type: "diverging",
      domain: [-30, 30],
      scheme: "RdBu",
      label: "Rainfall anomaly (%)",
      legend: true,
    },
    marks: [
      Plot.geo(geojson, {
        fill: f => {
          const name  = f.properties?.NAME_1 ?? f.properties?.ST_NM ?? "";
          return anomalyByState?.get(name) ?? 0;
        },
        stroke: "#fff",
        strokeWidth: 0.5,
        title: f => {
          const name   = f.properties?.NAME_1 ?? f.properties?.ST_NM ?? "Unknown";
          const anom   = anomalyByState?.get(name);
          return anom != null ? `${name}: ${anom > 0 ? "+" : ""}${anom}%` : name;
        },
      }),
    ],
  });
}
