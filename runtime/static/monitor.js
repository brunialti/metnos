/* Monitor: read-only polling and bounded uPlot charts. */
(() => {
  "use strict";
  const root = document.getElementById("monitor-root");
  const configNode = document.getElementById("monitor-config");
  if (!root || !configNode) return;
  const config = JSON.parse(configNode.textContent);
  const hasModelSeries = config.panels?.some(panel => panel.source === "models");
  const labels = config.labels;
  const form = document.getElementById("monitor-filters");
  const chartRoot = document.getElementById("monitor-charts");
  const seriesRoot = document.getElementById("monitor-series");
  const freshness = document.getElementById("monitor-freshness");
  const error = document.getElementById("monitor-error");
  const pauseButton = document.getElementById("monitor-pause");
  const exportLink = document.getElementById("monitor-export");
  const plots = new Map();
  const listeners = [];
  const palette = ["#0057d9", "#df2a00", "#008f28", "#d000c8", "#1b1b1b", "#b88700", "#008f9e", "#60209c", "#e45c98", "#795000", "#666d00", "#5d7f98"];
  const modelColors = new Map();
  let selected = null;
  let snapshot = null;
  let timer = null;
  let controller = null;
  let inFlight = false;
  let pending = false;
  let paused = false;
  let active = true;
  let disposed = false;
  let revision = 0;
  let selectionSignature = null;
  const initialParams = new URL(window.location.href).searchParams;
  let cursor = initialParams.get("cursor");
  const limit = initialParams.get("limit");
  const ownerId = initialParams.get("owner_id");
  let initialSelection = null;
  try {
    const keys = JSON.parse(initialParams.get("series_keys"));
    if (Array.isArray(keys) && keys.length <= 12 && keys.every(key => typeof key === "string" && key.length <= 1024)) {
      initialSelection = keys;
    }
  } catch (_) { /* Invalid URL selections are rejected by the server. */ }
  const number = new Intl.NumberFormat(config.locale, { maximumFractionDigits: 2 });
  const precise = new Intl.NumberFormat(config.locale, { maximumSignificantDigits: 6 });
  const date = new Intl.DateTimeFormat(config.locale, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZone: "UTC" });
  const tickTime = new Intl.DateTimeFormat(config.locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });
  const tickDay = new Intl.DateTimeFormat(config.locale, { day: "2-digit", month: "2-digit", timeZone: "UTC" });

  function timeTicks(plot, ticks) {
    const span = plot.scales.x.max - plot.scales.x.min;
    const format = span >= 86400 ? tickDay : tickTime;
    return ticks.map(stamp => format.format(new Date(stamp * 1000)));
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function listen(target, type, handler) {
    if (!target) return;
    target.addEventListener(type, handler);
    listeners.push(() => target.removeEventListener(type, handler));
  }
  function color(key) {
    const index = modelColors.get(key) ?? (key.endsWith("p95_ms") ? 1 : 0);
    return palette[index];
  }
  function identity(series) {
    const physical = series.physical_model_reported || series.physical_model_requested || labels.unknownModel;
    const suffix = series.identity_source === "configured" ? ` · ${labels.configured}` : "";
    const logical = series.model_grouping === "physical"
      ? (series.levels || []).map(level => level || labels.unknownModel).join(", ")
      : series.level;
    return `${logical || labels.unknownModel} → ${physical} · ${series.provider || labels.unknownProvider}${suffix}`;
  }
  function value(cell, factor) {
    return cell && typeof cell.value === "number" && Number.isFinite(cell.value) && cell.value >= 0 ? cell.value * factor : null;
  }
  function cells(metrics, key, length) {
    return Array.from({ length }, (_, index) => metrics?.[key]?.[index] ?? null);
  }
  function destroyPlots() {
    for (const item of plots.values()) item.plot.destroy();
    plots.clear();
    chartRoot?.replaceChildren();
  }
  function renderSelection(charts) {
    if (!seriesRoot) return;
    const series = charts.available_series || charts.series;
    const keys = new Set(series.map(item => item.key));
    if (selected === null) selected = new Set((charts.selected_series_keys ||
      charts.series.map(item => item.key)).filter(key => keys.has(key)).slice(0, 12));
    else selected = new Set([...selected].filter(key => keys.has(key)));
    for (const key of modelColors.keys()) if (!selected.has(key)) modelColors.delete(key);
    const used = new Set(modelColors.values());
    for (const key of selected) if (!modelColors.has(key)) {
      const index = palette.findIndex((_, index) => !used.has(index));
      modelColors.set(key, index); used.add(index);
    }
    const paint = (swatch, key) => {
      swatch.style.backgroundColor = selected.has(key) ? color(key) : "#737373";
    };
    const signature = series.map(item => `${item.key}\u0000${identity(item)}`).join("\n");
    if (signature === selectionSignature) {
      for (const input of seriesRoot.querySelectorAll("input")) {
        input.checked = selected.has(input.value);
        paint(input.nextElementSibling, input.value);
      }
      return;
    }
    selectionSignature = signature;
    seriesRoot.replaceChildren();
    for (const item of series) {
      const label = node("label", "monitor-series-item");
      const input = node("input");
      input.type = "checkbox";
      input.value = item.key;
      input.checked = selected.has(item.key);
      const swatch = node("span", "monitor-chart-marker");
      swatch.setAttribute("aria-hidden", "true");
      paint(swatch, item.key);
      label.append(input, swatch, node("span", "", identity(item)));
      seriesRoot.append(label);
    }
  }
  function curves(panel, charts) {
    const definitions = panel.metrics || [{ key: panel.id, label: panel.label }];
    const sources = panel.source === "requests"
      ? [{ key: "requests", label: null, metrics: charts.requests?.metrics || {} }]
      : charts.series.filter(item => selected.has(item.key)).map(item => ({ ...item, label: identity(item) }));
    return sources.flatMap(source => definitions.map(metric => ({
      key: `${source.key}:${metric.key}`,
      styleKey: panel.source === "requests" ? metric.key : source.key,
      label: source.label ? `${source.label} · ${metric.label}` : metric.label,
      cells: cells(source.metrics, metric.key, charts.bucket_starts.length),
    }))).slice(0, 12);
  }
  function renderCharts(charts) {
    if (!chartRoot || typeof window.uPlot !== "function") return;
    const xs = charts.bucket_starts.map(stamp => Date.parse(stamp) / 1000);
    if (xs.some(x => !Number.isFinite(x))) throw new Error("invalid_timestamps");
    for (const panel of config.panels || []) {
      const rows = curves(panel, charts);
      const signature = rows.map(row => `${row.key}\u0000${row.label}`).join("\n");
      const data = [xs, ...rows.map(row => row.cells.map(cell => value(cell, panel.factor ?? 1)))];
      let item = plots.get(panel.id);
      if (item && item.signature !== signature) {
        item.plot.destroy();
        item.card.remove();
        plots.delete(panel.id);
        item = null;
      }
      if (!item) {
        const card = node("section", "monitor-chart-card");
        card.dataset.metric = panel.id;
        card.append(node("h3", "", `${panel.label} · ${panel.unit}`));
        if (panel.source === "requests") {
          const legend = node("ul", "monitor-chart-legend");
          for (const row of rows) {
            const entry = node("li");
            const marker = node("span", "monitor-chart-marker");
            marker.style.backgroundColor = color(row.styleKey);
            marker.setAttribute("aria-hidden", "true");
            entry.append(marker, node("span", "", row.label));
            legend.append(entry);
          }
          card.append(legend);
        }
        const area = node("div", "monitor-chart-area");
        area.setAttribute("role", "img");
        area.setAttribute("aria-label", `${panel.label} · ${panel.unit}`);
        const detail = node("output", "monitor-chart-detail", labels.noData);
        card.append(area, detail);
        chartRoot.append(card);
        item = { card, area, detail, rows, signature, plot: null };
        const options = {
          width: Math.max(240, area.clientWidth || chartRoot.clientWidth), height: 240,
          scales: { x: { time: true }, y: { range: (u, min, max) => [0, Math.max(1, max || 0) * 1.1] } },
          series: [{}, ...rows.map(row => {
            const stroke = color(row.styleKey);
            return { label: row.label, stroke, paths: () => null, spanGaps: false,
              points: { show: true, size: 7, width: 1, fill: stroke } };
          })],
          axes: [{ values: timeTicks }, { size: 62, values: (u, ticks) => ticks.map(value => number.format(value)) }],
          legend: { show: false }, cursor: { drag: { x: false, y: false } },
          hooks: { setCursor: [plot => showSample(item, plot.cursor.idx, panel)] },
        };
        item.plot = new window.uPlot(options, data, area);
        plots.set(panel.id, item);
      } else {
        item.rows = rows;
        item.plot.setData(data);
      }
      chartRoot.append(item.card);
      showSample(item, null, panel);
    }
  }
  function showSample(item, index, panel) {
    if (!item) return;
    const at = Number.isInteger(index) ? index : (snapshot?.charts.bucket_starts.length || 0) - 1;
    const values = item.rows.map(row => {
      const cell = row.cells[at];
      const measured = value(cell, panel.factor ?? 1);
      const count = cell ? `${cell.known ?? 0}/${cell.total ?? 0}` : "0/0";
      const formula = cell && Number.isFinite(cell.numerator) && cell.numerator >= 0 &&
        Number.isFinite(cell.denominator_ms) && cell.denominator_ms > 0
        ? ` · ${labels.formula}: ${number.format(cell.numerator)} ${labels.tokenUnit} / ${precise.format(cell.denominator_ms / 1000)} ${labels.seconds}`
        : "";
      return `${row.label}: ${measured === null ? labels.unavailable : number.format(measured)} ${panel.unit}${formula} · ${labels.samples}: ${count}`;
    });
    const stamp = snapshot?.charts.bucket_starts[at];
    item.detail.textContent = values.length
      ? `${stamp ? `${labels.interval}: ${date.format(new Date(stamp))} UTC · ` : ""}${values.join("; ")}`
      : labels.noData;
  }
  function validate(payload) {
    const charts = payload.charts;
    if (!charts || !Array.isArray(charts.bucket_starts) || charts.bucket_starts.length > 500 || !Array.isArray(charts.series)) throw new Error("invalid_charts");
    const timestamps = charts.bucket_starts.map(stamp => Date.parse(stamp));
    if (timestamps.some((stamp, index) => !Number.isFinite(stamp) || (index > 0 && stamp <= timestamps[index - 1]))) throw new Error("invalid_timestamps");
    const keys = new Set();
    for (const series of charts.series) {
      if (typeof series.key !== "string" || keys.has(series.key)) throw new Error("invalid_series");
      keys.add(series.key);
    }
  }
  function render(payload) {
    validate(payload);
    snapshot = payload;
    for (const [id, key] of [["monitor-summary", "summary"], ["monitor-table", "requests"]]) {
      const target = document.getElementById(id);
      // Only the authenticated endpoint's escaped, server-rendered fragments enter HTML.
      if (target && typeof payload.html?.[key] === "string") {
        target.innerHTML = payload.html[key];
        window.htmx?.process(target);
      }
    }
    if (hasModelSeries) renderSelection(payload.charts);
    renderCharts(payload.charts);
    if (exportLink && typeof payload.export_url === "string") {
      const target = new URL(payload.export_url, window.location.href);
      if (target.origin === window.location.origin && target.pathname === "/admin/monitor/export") {
        exportLink.href = target.href;
      }
    }
    if (freshness) {
      const stamp = typeof payload.latest_event_at === "string" ? new Date(payload.latest_event_at) : null;
      const acquired = !stamp || Number.isNaN(stamp.getTime()) ? labels.unavailable : `${date.format(stamp)} UTC`;
      const stale = payload.health?.stale === true;
      freshness.textContent = `${labels.updated}: ${acquired}${stale ? ` · ${labels.stale}` : ""}`;
      freshness.classList.toggle("monitor-stale", stale);
    }
    if (error) error.textContent = "";
  }
  function url() {
    const target = new URL(root.dataset.endpoint, window.location.href);
    if (form) for (const [key, field] of new FormData(form)) target.searchParams.append(key, field);
    if (cursor) target.searchParams.set("cursor", cursor);
    if (limit && !target.searchParams.has("limit")) target.searchParams.set("limit", limit);
    if (ownerId && !target.searchParams.has("owner_id")) target.searchParams.set("owner_id", ownerId);
    const seriesKeys = selected === null ? initialSelection : [...selected];
    if (hasModelSeries && seriesKeys !== null) {
      target.searchParams.set("series_keys", JSON.stringify(seriesKeys.slice(0, 12)));
    }
    target.searchParams.set("view", config.view || root.dataset.view || "overview");
    const requestId = config.requestId || root.dataset.requestId;
    if (requestId) target.searchParams.set("request_id", requestId);
    return target;
  }
  function schedule(delay = 5000) {
    window.clearTimeout(timer);
    timer = null;
    if (active && !disposed && !paused && !document.hidden) timer = window.setTimeout(refresh, delay);
  }
  async function refresh() {
    window.clearTimeout(timer);
    timer = null;
    if (!active || disposed || paused || document.hidden) return;
    if (inFlight) { pending = true; return; }
    inFlight = true;
    pending = false;
    const requestRevision = revision;
    controller = new AbortController();
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller?.abort(); }, 10000);
    try {
      const response = await fetch(url(), { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal: controller.signal });
      if (!response.ok) throw new Error("request_failed");
      const payload = await response.json();
      if (active && !disposed && !paused && !document.hidden && requestRevision === revision) render(payload);
    } catch (problem) {
      if (active && !disposed && (problem.name !== "AbortError" || timedOut)) {
        if (error) error.textContent = labels.error;
        if (freshness) { freshness.textContent = labels.stale; freshness.classList.add("monitor-stale"); }
      }
    } finally {
      window.clearTimeout(timeout);
      controller = null;
      inFlight = false;
      schedule(pending ? 0 : 5000);
    }
  }
  function deactivate() {
    active = false;
    window.clearTimeout(timer);
    controller?.abort();
    observer?.disconnect();
    destroyPlots();
  }
  function dispose() {
    disposed = true;
    deactivate();
    for (const remove of listeners) remove();
  }
  const observer = typeof ResizeObserver === "function" ? new ResizeObserver(() => {
    for (const item of plots.values()) {
      const width = item.area.clientWidth;
      if (width > 0) item.plot.setSize({ width, height: 240 });
    }
  }) : null;
  if (chartRoot) observer?.observe(chartRoot);
  listen(seriesRoot, "change", event => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "checkbox" || selected === null) return;
    if (input.checked && selected.size >= 12) {
      input.checked = false;
      if (error) error.textContent = labels.seriesLimit;
      return;
    }
    if (input.checked) selected.add(input.value); else selected.delete(input.value);
    if (error) error.textContent = "";
    if (snapshot) renderCharts(snapshot.charts);
    revision += 1;
    controller?.abort();
    refresh();
  });
  function applyFilters() {
    revision += 1;
    selected = null;
    initialSelection = null;
    cursor = null;
    controller?.abort();
    refresh();
  }
  listen(form, "submit", event => {
    event.preventDefault();
    applyFilters();
  });
  listen(document.getElementById("monitor-model-grouping"), "change", applyFilters);
  listen(pauseButton, "click", () => {
    paused = !paused;
    pauseButton.textContent = paused ? labels.resume : labels.pause;
    pauseButton.setAttribute("aria-pressed", String(paused));
    if (paused) { window.clearTimeout(timer); controller?.abort(); } else refresh();
  });
  listen(document, "visibilitychange", () => {
    if (document.hidden) { window.clearTimeout(timer); controller?.abort(); }
    else refresh();
  });
  listen(window, "pagehide", event => { if (event.persisted) deactivate(); else dispose(); });
  listen(window, "pageshow", event => {
    if (!event.persisted || disposed) return;
    active = true;
    if (chartRoot) observer?.observe(chartRoot);
    refresh();
  });
  listen(document, "htmx:beforeCleanupElement", event => {
    if (event.target === root || event.target.contains?.(root)) dispose();
  });
  refresh();
})();
