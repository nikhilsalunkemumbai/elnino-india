/**
 * enso_gauge.js
 * Draws a D3 SVG arc gauge showing the current Niño3.4 anomaly on a −3 to +3 scale.
 *
 * Usage in Observable Markdown:
 *   import { ensoGauge } from "./components/enso_gauge.js";
 *   ensoGauge(latestENSO.anomaly)
 */

import * as d3 from "npm:d3";

/**
 * @param {number} value - Niño3.4 anomaly in °C
 * @param {object} opts
 * @returns {SVGElement}
 */
export function ensoGauge(value, { width = 300, height = 180 } = {}) {
  const MIN = -3, MAX = 3;
  const clamp = v => Math.max(MIN, Math.min(MAX, v));
  const clamped = clamp(value);

  const cx = width / 2, cy = height - 20;
  const outerR = Math.min(cx, cy) - 10;
  const innerR = outerR * 0.65;

  const startAngle = -Math.PI;
  const endAngle   =  0;
  const scale = d3.scaleLinear().domain([MIN, MAX]).range([startAngle, endAngle]);

  // Colour zones
  const zones = [
    { from: -3.0, to: -1.5, color: "#1565c0" },  // Strong La Niña
    { from: -1.5, to: -0.8, color: "#42a5f5" },  // Weak La Niña
    { from: -0.8, to:  0.8, color: "#bdbdbd" },  // Neutral
    { from:  0.8, to:  1.5, color: "#ffa726" },  // Weak El Niño
    { from:  1.5, to:  3.0, color: "#e53935" },  // Strong El Niño
  ];

  const arc = d3.arc().innerRadius(innerR).outerRadius(outerR);

  const svg = d3.create("svg")
    .attr("width", width)
    .attr("height", height)
    .attr("viewBox", `0 0 ${width} ${height}`);

  // Background arcs (colour zones)
  zones.forEach(z => {
    svg.append("path")
      .attr("d", arc({
        startAngle: scale(z.from),
        endAngle:   scale(z.to),
      }))
      .attr("transform", `translate(${cx},${cy})`)
      .attr("fill", z.color)
      .attr("opacity", 0.8);
  });

  // Needle
  const needleAngle = scale(clamped);
  const nx = (innerR - 5) * Math.cos(needleAngle - Math.PI / 2);
  const ny = (innerR - 5) * Math.sin(needleAngle - Math.PI / 2);

  svg.append("line")
    .attr("x1", cx).attr("y1", cy)
    .attr("x2", cx + nx).attr("y2", cy + ny)
    .attr("stroke", "#333")
    .attr("stroke-width", 3)
    .attr("stroke-linecap", "round");

  svg.append("circle")
    .attr("cx", cx).attr("cy", cy)
    .attr("r", 6)
    .attr("fill", "#333");

  // Value label
  svg.append("text")
    .attr("x", cx).attr("y", cy - outerR * 0.35)
    .attr("text-anchor", "middle")
    .attr("font-size", "1.6rem")
    .attr("font-weight", "bold")
    .attr("fill", clamped >= 0.8 ? "#e53935" : clamped <= -0.8 ? "#1565c0" : "#555")
    .text(`${value >= 0 ? "+" : ""}${value.toFixed(2)}°C`);

  // Axis ticks
  [-3, -2, -1, 0, 1, 2, 3].forEach(v => {
    const a = scale(v) - Math.PI / 2;
    const tx = cx + (outerR + 12) * Math.cos(a);
    const ty = cy + (outerR + 12) * Math.sin(a);
    svg.append("text")
      .attr("x", tx).attr("y", ty)
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "middle")
      .attr("font-size", "0.65rem")
      .attr("fill", "#666")
      .text(v);
  });

  return svg.node();
}
