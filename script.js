// script.js – treemap with multiline labels, duplication metric, adaptive fonts
/* global d3 */

/*******************************************************************
 * DOM & state
 *******************************************************************/
const fileInput    = document.getElementById("fileInput");
const metricSelect = document.getElementById("metricSelect");
const viz          = document.getElementById("viz");
const tooltip      = document.getElementById("tooltip");
const resetZoom    = document.getElementById("resetZoom");
let detailPane = null;
let hljsReady  = false;
let rootData   = null;
let currentMetric = metricSelect.value;
let currentScale = 1;
let offsetX = 0;
let offsetY = 0;
let zoomBehaviour = null;

/*******************************************************************
 * File loading & basic events
 *******************************************************************/
fileInput.addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const r = new FileReader();
  r.onload = (evt) => {
    try {
      rootData = JSON.parse(evt.target.result);
      draw();
    } catch { alert("Invalid JSON"); }
  };
  r.readAsText(file);
});
metricSelect.addEventListener("change", () => { currentMetric = metricSelect.value; rootData && draw(); });
window.addEventListener("resize", () => rootData && draw());
resetZoom.addEventListener("click", () => {
  if (!zoomBehaviour) return;
  currentScale = 1;
  offsetX = 0;
  offsetY = 0;
  d3.select(viz).select("svg")
    .transition()
    .duration(200)
    .call(zoomBehaviour.transform, d3.zoomIdentity);
});

/*******************************************************************
 * Helpers
 *******************************************************************/
const fullName = (d) =>
  d.data.nodetype === "method" && d.parent?.data?.name
    ? `${d.parent.data.name}.${d.data.name}`
    : d.data.name;

// Return the numeric value of the currently-selected metric for a leaf node
function metricValue(node) {
  const m = node.data.metrics || {};
  return currentMetric === "duplication"
    ? (m.duplication?.score ?? 0)
    : (m[currentMetric] ?? 0);
}

function injectHighlightJs() {
  if (hljsReady || document.getElementById("hljsScript")) return;
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css";
  document.head.appendChild(css);
  const s = document.createElement("script");
  s.id = "hljsScript";
  s.src = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js";
  s.onload = () => { hljsReady = true; document.dispatchEvent(new Event("hljs-ready")); };
  document.body.appendChild(s);
}

function ensurePane() {
  if (detailPane) return;
  detailPane = document.createElement("div");
  Object.assign(detailPane.style, {
    position: "fixed", top: 0, right: 0, width: "40%", height: "100%", background: "#fff",
    borderLeft: "1px solid #ddd", boxShadow: "-2px 0 8px rgba(0,0,0,.15)", overflowY: "auto",
    padding: "1rem 1.25rem", zIndex: 1000
  });
  document.body.appendChild(detailPane);
}

/*******************************************************************
 * Treemap draw
 *******************************************************************/
function draw() {
  viz.innerHTML = "";
  const { width } = viz.getBoundingClientRect();
  const height   = window.innerHeight - viz.getBoundingClientRect().top - 16;

  const root = d3.hierarchy(rootData, (d) => d.children)
    .sum((d) => d.metrics ? Math.log10((d.metrics.lines || 1) + 1) : 0)
    .sort((a, b) => b.value - a.value);
  d3.treemap().size([width, height]).padding(1)(root);

  const metricVals = root.leaves().map(metricValue);
  const color = d3.scaleSequential(d3.interpolateViridis).domain(d3.extent(metricVals));

  const svg = d3.select(viz).append("svg").attr("width", width).attr("height", height);
  const zoomRoot = svg.append("g");

  const node = zoomRoot.selectAll("g").data(root.leaves()).enter().append("g")
    .attr("transform", d => `translate(${d.x0},${d.y0})`)
    .on("click",      (e, d) => showDetail(d))
    .on("mousemove",  (e, d) => showTooltip(e, d))
    .on("mouseleave", hideTooltip);

  node.append("rect")
    .attr("class", "node")
    .attr("width",  d => d.x1 - d.x0)
    .attr("height", d => d.y1 - d.y0)
    .attr("fill",   d => color(metricValue(d)));

  const label = node.append("text").attr("pointer-events", "none").attr("font-weight", "700");

  label.each(function (d) {
    const sel = d3.select(this);
    const boxW = d.x1 - d.x0, boxH = d.y1 - d.y0;
    const name = fullName(d);

    const fs = Math.max(10, Math.min(12, Math.min(boxW / name.length * 1.6, boxH * 0.6)));

    const charsPerLine = Math.max(1, Math.floor(boxW / (fs * 0.55)));
    const lines = [];
    for (let i = 0; i < name.length; i += charsPerLine) lines.push(name.slice(i, i + charsPerLine));

    sel.attr("x", 2).attr("y", fs + 2).style("font-size", fs + "px");
    sel.selectAll("tspan").remove();
    lines.forEach((line, i) => sel.append("tspan").attr("x", 2).attr("y", fs + 2 + i * fs).text(line));

    const bg = d3.color(d3.select(this.parentNode).select("rect").attr("fill"));
    const lum = 0.2126 * (bg.r / 255) ** 2.2 + 0.7152 * (bg.g / 255) ** 2.2 + 0.0722 * (bg.b / 255) ** 2.2;
    sel.attr("fill", lum > 0.5 ? "#000" : "#fff");

    const blockHeight = lines.length * fs + 2;
    sel.style("opacity", boxW > 20 && boxH > blockHeight ? 1 : 0);

    sel.attr("data-font-size", fs).attr("data-lines", lines.length);
  });

  zoomBehaviour = d3
    .zoom()
    .scaleExtent([0.2, 4])
    .on("zoom", (e) => {
      const t = e.transform;
      currentScale = t.k;
      offsetX = t.x;
      offsetY = t.y;
      zoomRoot.attr("transform", t);
      resetZoom.classList.toggle("hidden", currentScale === 1);
      updateLabels();
    });

  svg.call(zoomBehaviour);
  applyZoom();
}

/*******************************************************************
 * Tooltip
 *******************************************************************/
function showTooltip(ev, d) {
  tooltip.innerHTML = `<strong>${fullName(d)}</strong><br/><small>${d.data.path || ""}</small>`;
  tooltip.classList.remove("hidden");
  positionTooltip(ev);
}
function hideTooltip() { tooltip.classList.add("hidden"); }
function positionTooltip(e) {
  const pad = 12, tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + tw > window.innerWidth - pad) x = e.clientX - tw - pad;
  if (y + th > window.innerHeight - pad) y = e.clientY - th - pad;
  tooltip.style.left = `${x}px`; tooltip.style.top = `${y}px`;
}
document.addEventListener("mousemove", e => !tooltip.classList.contains("hidden") && positionTooltip(e));

/*******************************************************************
 * Detail pane
 *******************************************************************/
function showDetail(d) {
  ensurePane(); injectHighlightJs();
  const { metrics = {}, source, path } = d.data;

  /* ---------- build metrics table, flattening duplication ---------- */
  const rows = [];
  const ordered = Object.entries(metrics).sort(([a], [b]) => a.localeCompare(b));
  for (const [k, v] of ordered) {
    const label = k.replace(/_/g, " ");
    if (k === "duplication") {
      rows.push(`<tr><td class="key">duplication</td><td>${v?.score ?? 0} ${v?.other ? `<small>→ ${v.other}</small>` : ""}</td></tr>`);
    } else {
      rows.push(`<tr><td class="key">${label}</td><td>${v}</td></tr>`);
    }
  }

  detailPane.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.5rem">
      <h2 style="margin:0;font-size:1rem;">${fullName(d)}</h2>
      <button id="closePane" style="font-size:1.25rem;border:none;background:none;cursor:pointer">&times;</button>
    </div>
    <p style="margin:0 0 .5rem 0;font-size:.8rem;color:#555">${path || ""}</p>
    <table style="font-size:.8rem;margin-bottom:.75rem">${rows.join("")}</table>
    ${source ? `<pre><code class="language-python">${escapeHtml(source)}</code></pre>` : "<p>No source</p>"}`;
  document.getElementById("closePane").onclick = () => { detailPane.remove(); detailPane = null; };

  if (hljsReady) window.hljs.highlightAll();
  else document.addEventListener("hljs-ready", () => window.hljs.highlightAll(), { once: true });
}

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]
  ));
}

function applyZoom() {
  const g = viz.querySelector("svg g");
  if (!g) return;
  g.setAttribute(
    "transform",
    `translate(${offsetX},${offsetY}) scale(${currentScale})`
  );
  resetZoom.classList.toggle("hidden", currentScale === 1);
  updateLabels();
}

function updateLabels() {
  d3.select(viz)
    .selectAll("svg g g")
    .each(function (d) {
      const textSel = d3.select(this).select("text");
      if (textSel.empty()) return;
      const baseFs = parseFloat(textSel.attr("data-font-size")) || 10;

    const fs = baseFs / currentScale;
    const boxW = (d.x1 - d.x0) * currentScale;
    const boxH = (d.y1 - d.y0) * currentScale;

    const charsPerLine = Math.max(1, Math.floor(boxW / (baseFs * 0.55)));
    const name = fullName(d);
    const lines = [];
    for (let i = 0; i < name.length; i += charsPerLine) lines.push(name.slice(i, i + charsPerLine));

    textSel.attr("x", 2).attr("y", fs + 2).style("font-size", fs + "px");
    textSel.selectAll("tspan").remove();
    lines.forEach((line, i) => textSel.append("tspan").attr("x", 2).attr("y", fs + 2 + i * fs).text(line));

    textSel.attr("data-lines", lines.length);
    const blockHeight = lines.length * fs + 2;
    textSel.style("opacity", boxW > 20 && boxH > blockHeight ? 1 : 0);
  });
}
