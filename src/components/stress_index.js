/**
 * stress_index.js
 * Renders a horizontal progress bar for the Monsoon Stress Index (0–100).
 *
 * Usage:
 *   import { stressBar } from "./components/stress_index.js";
 *   stressBar(stressScore)
 */

export function stressBar(score, { width = 400 } = {}) {
  const clamp   = v => Math.max(0, Math.min(100, v));
  const clamped = clamp(score);

  const color = clamped < 25 ? "#43a047"
              : clamped < 50 ? "#fb8c00"
              : clamped < 75 ? "#e53935"
              : "#7b1fa2";

  const label = clamped < 25 ? "Low Risk"
              : clamped < 50 ? "Moderate Risk"
              : clamped < 75 ? "High Risk"
              : "Severe Risk";

  const height = 48;
  const barH   = 20;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width",   width);
  svg.setAttribute("height",  height);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  // Track
  const track = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  track.setAttribute("x", 0); track.setAttribute("y", 4);
  track.setAttribute("width", width); track.setAttribute("height", barH);
  track.setAttribute("rx", barH / 2);
  track.setAttribute("fill", "#e0e0e0");
  svg.appendChild(track);

  // Fill
  const fill = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  fill.setAttribute("x", 0); fill.setAttribute("y", 4);
  fill.setAttribute("width", (clamped / 100) * width);
  fill.setAttribute("height", barH);
  fill.setAttribute("rx", barH / 2);
  fill.setAttribute("fill", color);
  svg.appendChild(fill);

  // Score text
  const scoreText = document.createElementNS("http://www.w3.org/2000/svg", "text");
  scoreText.setAttribute("x", (clamped / 100) * width + 6);
  scoreText.setAttribute("y", 4 + barH / 2 + 5);
  scoreText.setAttribute("font-size", "0.85rem");
  scoreText.setAttribute("font-weight", "bold");
  scoreText.setAttribute("fill", "#333");
  scoreText.textContent = `${clamped}/100 — ${label}`;
  svg.appendChild(scoreText);

  // Zone labels
  const zones = [{ x: 0, t: "Low" }, { x: width * 0.25, t: "Moderate" },
                 { x: width * 0.5, t: "High" }, { x: width * 0.75, t: "Severe" }];
  zones.forEach(z => {
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", z.x + 4); t.setAttribute("y", height - 2);
    t.setAttribute("font-size", "0.6rem"); t.setAttribute("fill", "#888");
    t.textContent = z.t;
    svg.appendChild(t);
  });

  return svg;
}
