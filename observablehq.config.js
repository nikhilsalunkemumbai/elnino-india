// observablehq.config.js
export default {
  title: "India El Niño Dashboard",
  root: "src",
  // Expose the repo-root data/ directory so FileAttachment can load
  // JSON/CSV files committed by GitHub Actions. Observable Framework
  // blocks ../ traversal above src/, so we symlink data/ into src/.
  // See: scripts in package.json copy data/ before build.
  pages: [
    { name: "Dashboard", path: "/dashboard" },
    { name: "About", path: "/about" },
    { name: "Privacy Policy", path: "/privacy" }
  ],
  footer: "Built with Observable Framework · Data: NOAA, NOAA PSL, CHIRPS v3, CWC RSMS · Updated daily · MIT License"
};
