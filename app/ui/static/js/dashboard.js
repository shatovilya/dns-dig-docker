(function () {
  "use strict";

  const cfg = window.DNS_DEBUG_UI || { basePath: "/dns-debug", refreshSeconds: 5 };
  const API = cfg.basePath + "/api/ui";
  const CORE_API = cfg.basePath.replace(/\/dns-debug$/, "") || "";
  const I18N = window.DnsDebugI18n;

  function t(key, params) {
    return I18N && I18N.t ? I18N.t(key, params) : key;
  }

  function fmtPct(value, digits) {
    return I18N && I18N.formatPercent ? I18N.formatPercent(value, digits) : ((Number(value) || 0) * 100).toFixed(digits != null ? digits : 1) + "%";
  }

  function fmtNum(value, options) {
    return I18N && I18N.formatNumber ? I18N.formatNumber(value, options) : String(value ?? "—");
  }

  function fmtMs(value) {
    return I18N && I18N.formatDurationMs ? I18N.formatDurationMs(value) : (value || 0) + " ms";
  }

  function translateSignal(signal) {
    return I18N && I18N.translateSignal ? I18N.translateSignal(signal) : (signal && signal.message) || "";
  }

  function translateDataSource(source) {
    return I18N && I18N.translateDataSource ? I18N.translateDataSource(source) : source;
  }
  const charts = {};
  const narrowChart = window.matchMedia("(max-width: 768px)");
  let viewMode = "live";
  let refreshTimer = null;
  let autoRefreshEnabled = true;
  let recordsRows = [];
  let recordsSort = { key: "errors", dir: -1 };
  let loading = false;
  let firstLoad = true;
  let lastEnvelope = null;
  let lastRankingsData = null;
  let previousOverview = null;
  let previousLatency = null;
  let previousKpiExtras = null;
  let clientSearch = "";
  let clientStatusFilter = "";
  let latencySeriesVisible = { p50: true, p95: true, p99: false };

  function $(id) {
    return document.getElementById(id);
  }

  function themeInit() {
    const saved = localStorage.getItem("dns-debug-theme") || "dark";
    document.documentElement.setAttribute("data-theme", saved);
    $("theme-toggle").addEventListener("click", () => {
      const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("dns-debug-theme", next);
      refreshChartsTheme();
    });
  }

  function chartColors() {
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    const root = getComputedStyle(document.documentElement);
    return {
      text: dark ? "#8b98a5" : "#5c6570",
      grid: dark ? "#2f3b4a" : "#d8dee4",
      palette: ["#3b82f6", "#22c55e", "#eab308", "#ef4444", "#a855f7", "#38bdf8"],
      ok: root.getPropertyValue("--ok").trim() || "#22c55e",
      warn: root.getPropertyValue("--warn").trim() || "#eab308",
      crit: root.getPropertyValue("--crit").trim() || "#ef4444",
    };
  }

  function chartLegendPosition() {
    return narrowChart.matches ? "bottom" : "top";
  }

  function refreshChartsTheme() {
    const c = chartColors();
    Object.values(charts).forEach((ch) => {
      if (!ch) return;
      if (ch.options.scales) {
        Object.values(ch.options.scales).forEach((s) => {
          if (s.ticks) s.ticks.color = c.text;
          if (s.grid) s.grid.color = c.grid;
        });
      }
      if (ch.options.plugins && ch.options.plugins.legend) {
        ch.options.plugins.legend.labels.color = c.text;
        ch.options.plugins.legend.position = chartLegendPosition();
      }
      ch.update("none");
    });
  }

  function destroyChart(id) {
    if (charts[id]) {
      charts[id].destroy();
      delete charts[id];
    }
  }

  function destroyAllCharts() {
    Object.keys(charts).forEach(destroyChart);
  }

  function destroyModeAffectedCharts() {
    destroyChart("chart-errors-class");
  }

  function fromDatetimeLocal(value) {
    if (!value) return "";
    return new Date(value).toISOString();
  }

  function applyLiveTimePreset() {
    const preset = $("live-time-preset").value;
    if (!preset || viewMode !== "live") return { from: null, to: null };
    const mins = parseInt(preset, 10);
    const to = new Date();
    const from = new Date(to.getTime() - mins * 60 * 1000);
    return { from: from.toISOString(), to: to.toISOString() };
  }

  function queryParams() {
    const p = new URLSearchParams();
    const testId = $("filter-test-id").value;
    const mode = $("filter-resolve-mode").value;
    const qtype = $("filter-query-type").value;
    if (testId) p.set("test_id", testId);
    if (mode) p.set("resolve_mode", mode);
    if (qtype) p.set("query_type", qtype);
    p.set("view_mode", viewMode);

    if (viewMode === "historical") {
      const snap = $("filter-snapshot").value;
      if (snap) {
        p.set("snapshot_id", snap);
      } else {
        const from = fromDatetimeLocal($("filter-from").value);
        const to = fromDatetimeLocal($("filter-to").value);
        if (from) p.set("from", from);
        if (to) p.set("to", to);
      }
    } else if (viewMode === "live") {
      const liveRange = applyLiveTimePreset();
      if (liveRange.from) p.set("from", liveRange.from);
      if (liveRange.to) p.set("to", liveRange.to);
    }
    const qs = p.toString();
    return qs ? "?" + qs : "";
  }

  function compareParams() {
    const p = new URLSearchParams();
    const testId = $("filter-test-id").value;
    const mode = $("filter-resolve-mode").value;
    const qtype = $("filter-query-type").value;
    if (testId) p.set("test_id", testId);
    if (mode) p.set("resolve_mode", mode);
    if (qtype) p.set("query_type", qtype);

    const bSnap = $("baseline-snapshot").value;
    const cSnap = $("compare-snapshot").value;
    const bTest = $("baseline-test-id").value;
    const cTest = $("compare-test-id").value;
    const bMode = $("baseline-resolve-mode").value;
    const cMode = $("compare-resolve-mode").value;
    if (bTest) p.set("baseline_test_id", bTest);
    if (cTest) p.set("compare_test_id", cTest);
    if (bMode) p.set("baseline_resolve_mode", bMode);
    if (cMode) p.set("compare_resolve_mode", cMode);

    if (bSnap) p.set("baseline_snapshot_id", bSnap);
    else {
      const bf = fromDatetimeLocal($("baseline-from").value);
      const bt = fromDatetimeLocal($("baseline-to").value);
      if (bf) p.set("baseline_from", bf);
      if (bt) p.set("baseline_to", bt);
    }
    if (cSnap) p.set("compare_snapshot_id", cSnap);
    else {
      const cf = fromDatetimeLocal($("compare-from").value);
      const ct = fromDatetimeLocal($("compare-to").value);
      if (cf) p.set("compare_from", cf);
      if (ct) p.set("compare_to", ct);
    }
    const qs = p.toString();
    return qs ? "?" + qs : "";
  }

  function comparisonQueryParams() {
    const p = new URLSearchParams();
    const testId = $("compare-test-id").value || $("filter-test-id").value;
    const mode = $("compare-resolve-mode").value || $("filter-resolve-mode").value;
    const qtype = $("filter-query-type").value;
    if (testId) p.set("test_id", testId);
    if (mode) p.set("resolve_mode", mode);
    if (qtype) p.set("query_type", qtype);
    p.set("view_mode", "historical");
    const cSnap = $("compare-snapshot").value;
    if (cSnap) p.set("snapshot_id", cSnap);
    else {
      const cf = fromDatetimeLocal($("compare-from").value);
      const ct = fromDatetimeLocal($("compare-to").value);
      if (cf) p.set("from", cf);
      if (ct) p.set("to", ct);
    }
    const qs = p.toString();
    return qs ? "?" + qs : "";
  }

  async function fetchComparisonJson(path) {
    const res = await fetch(API + path + comparisonQueryParams());
    if (!res.ok) throw new Error(path + " " + res.status);
    return res.json();
  }

  async function fetchJson(path, useCompare) {
    const qs = useCompare ? compareParams() : queryParams();
    const res = await fetch(API + path + qs);
    if (!res.ok) throw new Error(path + " " + res.status);
    return res.json();
  }

  function computeTrend(current, previous, invert) {
    if (previous === null || previous === undefined || current === null || current === undefined) {
      return null;
    }
    const cur = Number(current);
    const prev = Number(previous);
    if (Number.isNaN(cur) || Number.isNaN(prev)) return null;
    const delta = cur - prev;
    if (prev === 0) return { delta, pct: null, dir: delta > 0 ? "up" : delta < 0 ? "down" : "flat" };
    const pct = (delta / prev) * 100;
    let dir = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
    if (invert) {
      dir = delta > 0 ? "down-bad" : delta < 0 ? "up-good" : "flat";
    }
    return { delta, pct, dir };
  }

  function renderKpi(label, value, cls, opts) {
    opts = opts || {};
    let trendHtml = "";
    if (opts.trend) {
      const sign = opts.trend.delta > 0 ? "+" : "";
      const pct = opts.trend.pct !== null ? " (" + sign + opts.trend.pct.toFixed(1) + "%)" : "";
      const arrow = opts.trend.dir === "up" || opts.trend.dir === "down-bad" ? "▲" : opts.trend.dir === "down" || opts.trend.dir === "up-good" ? "▼" : "—";
      const trendCls = opts.trend.dir === "up-good" || (opts.trend.dir === "down" && !opts.invertTrend) ? "trend-good" :
        opts.trend.dir === "down-bad" || (opts.trend.dir === "up" && opts.invertTrend) ? "trend-bad" : "trend-flat";
      trendHtml = '<div class="kpi-trend ' + trendCls + '">' + arrow + sign + opts.trend.delta.toFixed(2) + pct + "</div>";
    }
    const tooltip = opts.tooltip ? ' title="' + opts.tooltip.replace(/"/g, "&quot;") + '"' : "";
    const drill = opts.drilldown ? ' data-drilldown="' + opts.drilldown + '"' : "";
    const clickable = opts.drilldown ? " kpi-clickable" : "";
    return (
      '<div class="kpi ' + (cls || "") + clickable + '"' + tooltip + drill + ">" +
      '<div class="label">' + label + "</div>" +
      '<div class="value">' + value + "</div>" +
      trendHtml +
      "</div>"
    );
  }

  function kpi(label, value, cls) {
    return renderKpi(label, value, cls);
  }

  function deltaKpi(label, delta, invert) {
    if (!delta || delta.absolute === null) {
      return kpi(label, t("common.na"), "info");
    }
    const sign = delta.absolute > 0 ? "+" : "";
    const pct = delta.percent !== null ? " (" + sign + delta.percent + "%)" : "";
    let cls = "";
    if (invert) {
      cls = delta.absolute < 0 ? "ok" : delta.absolute > 0 ? "crit" : "";
    } else {
      cls = delta.absolute < 0 ? "ok" : delta.absolute > 0 ? "warn" : "";
    }
    return kpi(label, sign + delta.absolute + pct, cls);
  }

  function ensureChart(id, type, data, options) {
    const el = $(id);
    if (!el || typeof Chart === "undefined") return null;
    if (charts[id] && charts[id].config.type !== type) {
      destroyChart(id);
    }
    const c = chartColors();
    const baseOpts = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: chartLegendPosition(),
          labels: { color: c.text, boxWidth: 12 },
        },
        tooltip: { mode: "index", intersect: false },
      },
      scales: type !== "doughnut" && type !== "pie" ? {
        x: { ticks: { color: c.text, maxRotation: 45 }, grid: { color: c.grid } },
        y: { ticks: { color: c.text }, grid: { color: c.grid } },
      } : undefined,
    };
    if (type === "doughnut") {
      baseOpts.cutout = "60%";
    }
    const merged = Object.assign({}, baseOpts, options || {});
    if (charts[id]) {
      charts[id].data = data;
      Object.assign(charts[id].options, merged);
      if (charts[id].options.plugins && charts[id].options.plugins.legend) {
        charts[id].options.plugins.legend.position = chartLegendPosition();
      }
      charts[id].update("none");
      return charts[id];
    }
    charts[id] = new Chart(el, { type, data, options: merged });
    return charts[id];
  }

  function setModeUi() {
    destroyModeAffectedCharts();
    document.querySelectorAll(".mode-btn").forEach((btn) => {
      const active = btn.dataset.mode === viewMode;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
      btn.setAttribute("tabindex", active ? "0" : "-1");
    });
    $("mode-badge").textContent = I18N && I18N.translateViewMode
      ? I18N.translateViewMode(viewMode)
      : viewMode.charAt(0).toUpperCase() + viewMode.slice(1);
    document.body.dataset.viewMode = viewMode;
    document.querySelectorAll(".historical-only").forEach((el) => {
      el.classList.toggle("hidden", viewMode !== "historical");
    });
    document.querySelectorAll(".compare-only").forEach((el) => {
      el.classList.toggle("hidden", viewMode !== "compare");
    });
    document.querySelectorAll(".live-only").forEach((el) => {
      el.classList.toggle("hidden", viewMode !== "live");
    });
    $("compare-deltas").classList.toggle("hidden", viewMode !== "compare");
    if (viewMode !== "live") {
      autoRefreshEnabled = false;
      updateAutoRefreshButton();
    }
    setupRefreshTimer();
  }

  function updateAutoRefreshButton() {
    const btn = $("auto-refresh-toggle");
    if (!btn) return;
    btn.textContent = autoRefreshEnabled ? t("filters.auto_refresh_on") : t("filters.auto_refresh_off");
    btn.setAttribute("aria-pressed", autoRefreshEnabled ? "true" : "false");
    btn.classList.toggle("active", autoRefreshEnabled);
  }

  function setupRefreshTimer() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
    if (viewMode === "live" && autoRefreshEnabled) {
      refreshTimer = setInterval(refreshAll, (cfg.refreshSeconds || 5) * 1000);
    }
  }

  function setLoading(on) {
    loading = on;
    document.body.classList.toggle("is-loading", on);
    const panels = ["overview", "latency", "edns", "errors", "garbage", "cache", "records", "load", "mtr", "rankings"];
    panels.forEach((name) => {
      const sk = $("skeleton-" + name);
      if (sk) sk.classList.toggle("hidden", !on || !firstLoad);
    });
  }

  function showBanner(id, text) {
    const el = $(id);
    if (!text) {
      el.classList.add("hidden");
      el.textContent = "";
      return;
    }
    el.classList.remove("hidden");
    if (id === "error-banner") {
      $("error-banner-text").textContent = text;
    } else {
      el.textContent = text;
    }
  }

  function setPanelBadge(panel, data) {
    const badge = $("badge-" + panel);
    if (!badge || !data) return;
    const source = data.data_source;
    if (source && viewMode !== "live") {
      badge.textContent = translateDataSource(source);
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  }

  function renderGlobalStatus(data) {
    const gs = (data && data.global_status) || { level: "ok", signals: [] };
    const levelEl = $("global-status-level");
    const levelLabel = I18N && I18N.translateLevel ? I18N.translateLevel(gs.level) : gs.level;
    levelEl.textContent = String(levelLabel).toUpperCase();
    levelEl.className = "global-status-level status-" + gs.level;
    const list = $("global-status-signals");
    list.innerHTML = (gs.signals || []).map((s) => "<li>" + translateSignal(s) + "</li>").join("");
    const badge = $("health-badge");
    badge.textContent = levelLabel;
    badge.className = "badge badge-" + (gs.level === "ok" ? "ok" : gs.level === "degraded" ? "warn" : "crit");
  }

  function renderResolverContext(data) {
    const r = (data && data.resolver) || {};
    $("resolver-context").innerHTML =
      "<span>" + t("resolver.nameservers") + ": <strong>" + (r.nameservers || []).join(", ") + "</strong></span>" +
      " · <span>" + t("resolver.search_domains") + ": <strong>" + (r.search_domains_count || 0) + "</strong></span>" +
      " · <span>" + t("resolver.ndots") + ": <strong>" + (r.ndots != null ? r.ndots : "—") + "</strong></span>";
  }

  function renderFilterChips(data, compareData) {
    const chips = $("filter-chips");
    const parts = [];

    if (clientSearch) parts.push({ key: "search", label: t("filters.chip_search", { value: clientSearch }) });
    if (clientStatusFilter) parts.push({ key: "status", label: t("filters.chip_status", { value: clientStatusFilter }) });

    if (viewMode === "compare" && compareData) {
      const base = compareData.baseline?.filters_applied || {};
      const comp = compareData.comparison?.filters_applied || {};
      if (base.baseline_snapshot_id || base.baseline_from || base.baseline_to) {
        const label = base.baseline_snapshot_id
          ? t("filters.chip_baseline_snapshot", { value: base.baseline_snapshot_id })
          : t("filters.chip_baseline_range", { from: base.baseline_from || "…", to: base.baseline_to || "…" });
        parts.push({ key: "baseline_range", label: label });
      }
      if (comp.compare_snapshot_id || comp.compare_from || comp.compare_to) {
        const label = comp.compare_snapshot_id
          ? t("filters.chip_compare_snapshot", { value: comp.compare_snapshot_id })
          : t("filters.chip_compare_range", { from: comp.compare_from || "…", to: comp.compare_to || "…" });
        parts.push({ key: "compare_range", label: label });
      }
      if (base.baseline_resolve_mode) parts.push({ key: "baseline_mode", label: t("filters.chip_baseline_mode", { value: base.baseline_resolve_mode }) });
      if (comp.compare_resolve_mode) parts.push({ key: "compare_mode", label: t("filters.chip_compare_mode", { value: comp.compare_resolve_mode }) });
    }

    const applied = (data && data.filters_applied) || {};
    if (applied.test_id) parts.push({ key: "test_id", label: t("filters.chip_test", { value: applied.test_id }) });
    if (applied.resolve_mode) parts.push({ key: "resolve_mode", label: t("filters.chip_mode", { value: applied.resolve_mode }) });
    if (applied.query_type) parts.push({ key: "query_type", label: t("filters.chip_type", { value: applied.query_type }) });
    if (applied.from || applied.to) {
      parts.push({ key: "range", label: t("filters.chip_range", { from: applied.from || "…", to: applied.to || "…" }) });
    }
    if (applied.snapshot_id) parts.push({ key: "snapshot_id", label: t("filters.chip_snapshot", { value: applied.snapshot_id }) });

    chips.innerHTML = parts.map((p) =>
      '<button type="button" class="chip" data-chip="' + p.key + '">' + p.label + " ×</button>"
    ).join("");
    chips.querySelectorAll(".chip").forEach((btn) => {
      btn.addEventListener("click", () => clearChip(btn.dataset.chip));
    });
  }

  function clearChip(key) {
    if (key === "test_id") $("filter-test-id").value = "";
    if (key === "resolve_mode") $("filter-resolve-mode").value = "";
    if (key === "query_type") $("filter-query-type").value = "";
    if (key === "search") { clientSearch = ""; $("filter-search").value = ""; }
    if (key === "status") { clientStatusFilter = ""; $("filter-status").value = ""; }
    if (key === "range") {
      $("filter-from").value = "";
      $("filter-to").value = "";
      $("live-time-preset").value = "";
    }
    if (key === "snapshot_id") $("filter-snapshot").value = "";
    if (key === "baseline_range") {
      $("baseline-from").value = "";
      $("baseline-to").value = "";
      $("baseline-snapshot").value = "";
    }
    if (key === "compare_range") {
      $("compare-from").value = "";
      $("compare-to").value = "";
      $("compare-snapshot").value = "";
    }
    if (key === "baseline_mode") $("baseline-resolve-mode").value = "";
    if (key === "compare_mode") $("compare-resolve-mode").value = "";
    refreshAll();
  }

  function resetAllFilters() {
    $("filter-test-id").value = "";
    $("filter-resolve-mode").value = "";
    $("filter-query-type").value = "";
    $("filter-from").value = "";
    $("filter-to").value = "";
    $("filter-snapshot").value = "";
    $("live-time-preset").value = "";
    $("filter-search").value = "";
    $("filter-status").value = "";
    clientSearch = "";
    clientStatusFilter = "";
    $("baseline-from").value = "";
    $("baseline-to").value = "";
    $("compare-from").value = "";
    $("compare-to").value = "";
    $("baseline-snapshot").value = "";
    $("compare-snapshot").value = "";
    $("baseline-test-id").value = "";
    $("compare-test-id").value = "";
    $("baseline-resolve-mode").value = "";
    $("compare-resolve-mode").value = "";
    refreshAll();
  }

  function historicalScopeSelected() {
    return !!(
      $("filter-snapshot").value ||
      $("filter-from").value ||
      $("filter-to").value
    );
  }

  function compareScopeSelected() {
    const hasBaseline = !!(
      $("baseline-snapshot").value ||
      $("baseline-from").value ||
      $("baseline-to").value
    );
    const hasComparison = !!(
      $("compare-snapshot").value ||
      $("compare-from").value ||
      $("compare-to").value
    );
    return hasBaseline && hasComparison;
  }

  function renderWarnings(data) {
    const warnings = (data && data.warnings) || [];
    const retention = (data && data.retention) || {};
    const msgs = [];
    if (warnings.includes("event_buffer_truncated")) {
      msgs.push(t("history.warnings.event_buffer_truncated", { limit: retention.event_buffer_size || "?" }));
    }
    if (warnings.includes("snapshot_retention_at_limit")) {
      msgs.push(t("history.warnings.snapshot_retention_at_limit", { limit: retention.snapshot_retention_count || "?" }));
    }
    if (data && data.retention && (data.retention.db_enabled || data.retention.retention_days)) {
      const days = data.retention.db_retention_days || data.retention.retention_days || 7;
      msgs.push(
        data.retention.db_enabled
          ? t("history.warnings.db_retention_info", { days: days })
          : t("history.warnings.retention_days_info", { days: days })
      );
    }
    if (warnings.includes("outside_retention_window")) {
      const days = retention.db_retention_days || retention.retention_days || 7;
      msgs.push(t("history.warnings.outside_retention_window", { days: days }));
    }
    if (warnings.includes("db_unavailable")) {
      msgs.push(t("history.warnings.db_unavailable"));
    }
    if (warnings.includes("mtr_history_at_limit")) {
      msgs.push(t("history.warnings.mtr_history_at_limit", { limit: retention.mtr_max_history || "?" }));
    }
    if (data && data.is_stale && viewMode === "historical") {
      msgs.push(t("history.warnings.stale_historical"));
    }
    if (viewMode === "historical" && !historicalScopeSelected()) {
      msgs.push(t("history.select_scope"));
    }
    showBanner("warn-banner", msgs.join(" "));
    const source = data && data.data_source ? data.data_source : "";
    const dsBadge = $("data-source-badge");
    if (dsBadge) {
      if (source && viewMode !== "live") {
        dsBadge.textContent = translateDataSource(source);
        dsBadge.classList.remove("hidden");
      } else {
        dsBadge.classList.add("hidden");
      }
    }
    const range = data && data.time_range;
    let rangeText = "";
    if (range && (range.from || range.to)) {
      rangeText = " · " + (range.from || "…") + " – " + (range.to || "…");
    } else if (range && range.snapshot_id) {
      rangeText = " · " + t("history.snapshot_prefix") + " " + range.snapshot_id;
    }
    const updatedPrefix = data && data.last_update ? t("common.updated") + " " + data.last_update : "—";
    $("last-update").textContent = updatedPrefix + (source ? " · " + translateDataSource(source) : "") + rangeText;

    const running = ((data && data.tests) || []).filter((tst) => tst.status === "running");
    if (running.length && viewMode === "live") {
      const prog = running.map((tst) => tst.test_name + " " + Math.round((tst.progress || 0) * 100) + "%").join(", ");
      showBanner("info-banner", t("states.tests_running", { progress: prog }));
    } else if (viewMode !== "compare" || compareScopeSelected()) {
      if (!(viewMode === "historical" && !historicalScopeSelected())) {
        const info = $("info-banner");
        const noSnapPrefix = t("history.no_snapshots").slice(0, 20);
        if (info && info.textContent && info.textContent.indexOf(noSnapPrefix) === 0) {
          /* keep snapshot empty message */
        } else if (!msgs.length) {
          showBanner("info-banner", "");
        }
      }
    }
  }

  function resetPanels(message) {
    destroyAllCharts();
    const msg = message || "";
    ["overview", "latency", "edns", "errors", "garbage", "cache", "records", "load", "mtr", "rankings"].forEach((name) => {
      panelState("state-" + name, msg, "empty");
    });
    $("overview-kpis").innerHTML = "";
    $("compare-deltas").innerHTML = "";
    $("latency-kpis").innerHTML = "";
    $("garbage-summary").innerHTML = "";
    $("top-noisy-domains").innerHTML = "";
    $("cache-kpis").innerHTML = "";
    $("load-kpis").innerHTML = "";
    $("rankings-grid").innerHTML = "";
    document.querySelector("#records-table tbody").innerHTML = "";
    document.querySelector("#mtr-table tbody").innerHTML = "";
    $("mtr-verdict").textContent = "";
    $("error-matrix-wrap").classList.add("hidden");
  }

  function panelState(id, message, type) {
    const el = $(id);
    if (!message) {
      el.classList.add("hidden");
      el.textContent = "";
      return;
    }
    el.classList.remove("hidden");
    el.className = "panel-state state-" + (type || "empty");
    el.textContent = message;
  }

  function scrollToPanel(panelId) {
    const el = document.getElementById(panelId);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function bindKpiDrilldowns() {
    document.querySelectorAll("[data-drilldown]").forEach((el) => {
      el.addEventListener("click", () => {
        const target = el.dataset.drilldown;
        if (target) scrollToPanel(target);
      });
    });
  }

  function renderOverview(data, compareData, latencyData) {
    lastEnvelope = data;
    renderFilterChips(data, compareData);
    renderWarnings(data);
    renderGlobalStatus(data);
    renderResolverContext(data);
    setPanelBadge("overview", data);

    const sel = $("filter-test-id");
    const current = sel.value;
    const testOpts = (tests) =>
      '<option value="">' + t("filters.all_tests") + "</option>" +
      tests.map((tst) => '<option value="' + tst.test_id + '">' + tst.test_name + " (" + tst.status + ")</option>").join("");
    if (data.tests && data.tests.length) {
      const opts = testOpts(data.tests);
      if (sel.innerHTML !== opts) sel.innerHTML = opts;
      sel.value = current;
      ["baseline-test-id", "compare-test-id"].forEach((id) => {
        const el = $(id);
        if (!el) return;
        const cur = el.value;
        el.innerHTML = '<option value="">' + t("filters.same_as_test") + "</option>" +
          data.tests.map((tst) => '<option value="' + tst.test_id + '">' + tst.test_name + "</option>").join("");
        el.value = cur;
      });
    }

    if ((data.total_queries || 0) === 0 && viewMode !== "compare") {
      panelState("state-overview", t("states.no_queries"), "empty");
    } else {
      panelState("state-overview", data.warnings && data.warnings.length ? t("states.partial_metrics") : "");
    }

    const extras = data.kpi_extras || {};
    const lat = latencyData || {};
    const ratio = fmtPct(data.success_ratio || 0, 1);
    const errCls = (data.error_count || 0) > 0 ? "warn" : "ok";

    if (viewMode === "live") {
      $("overview-kpis").innerHTML =
        renderKpi(t("kpis.p50"), fmtMs(extras.p50_ms || lat.p50 || 0), "info", {
          trend: computeTrend(extras.p50_ms || lat.p50, previousKpiExtras && previousKpiExtras.p50_ms, true),
          tooltip: t("kpis.tooltip_p50"),
          drilldown: "panel-latency",
          invertTrend: true,
        }) +
        renderKpi(t("kpis.p95"), fmtMs(extras.p95_ms || lat.p95 || 0), "warn", {
          trend: computeTrend(extras.p95_ms || lat.p95, previousKpiExtras && previousKpiExtras.p95_ms, true),
          tooltip: t("kpis.tooltip_p95"),
          drilldown: "panel-latency",
          invertTrend: true,
        }) +
        renderKpi(t("kpis.p99"), fmtMs(extras.p99_ms || lat.p99 || 0), "crit", {
          trend: computeTrend(extras.p99_ms || lat.p99, previousKpiExtras && previousKpiExtras.p99_ms, true),
          tooltip: t("kpis.tooltip_p99"),
          drilldown: "panel-latency",
          invertTrend: true,
        }) +
        renderKpi(t("kpis.error_rate"), fmtPct(extras.error_rate || 0, 1), errCls, {
          trend: computeTrend(extras.error_rate, previousKpiExtras && previousKpiExtras.error_rate, true),
          tooltip: t("kpis.tooltip_error_rate"),
          drilldown: "panel-errors",
          invertTrend: true,
        }) +
        renderKpi(t("kpis.nxdomain_rate"), fmtPct(extras.nxdomain_rate || 0, 1), "warn", {
          trend: computeTrend(extras.nxdomain_rate, previousKpiExtras && previousKpiExtras.nxdomain_rate, true),
          tooltip: t("kpis.tooltip_nxdomain_rate"),
          drilldown: "panel-errors",
          invertTrend: true,
        }) +
        renderKpi(t("kpis.noisy_ratio"), fmtPct(extras.noisy_ratio || 0, 1), "warn", {
          trend: computeTrend(extras.noisy_ratio, previousKpiExtras && previousKpiExtras.noisy_ratio, true),
          tooltip: t("kpis.tooltip_noisy_ratio"),
          drilldown: "panel-garbage",
          invertTrend: true,
        }) +
        renderKpi(t("kpis.cache_hit_heuristic"), fmtPct(extras.cache_hit_ratio || 0, 2), "info", {
          trend: computeTrend(extras.cache_hit_ratio, previousKpiExtras && previousKpiExtras.cache_hit_ratio, false),
          tooltip: t("kpis.tooltip_cache_hit"),
          drilldown: "panel-cache",
        }) +
        renderKpi(t("kpis.mtr_degraded"), extras.mtr_degraded_count ?? 0, extras.mtr_degraded_count > 0 ? "crit" : "ok", {
          tooltip: t("kpis.tooltip_mtr_degraded"),
          drilldown: "panel-mtr",
        }) +
        kpi(t("kpis.total_queries"), data.total_queries ?? 0) +
        kpi(t("kpis.success_pct"), ratio, (data.success_ratio || 0) > 0.9 ? "ok" : "warn");

      previousOverview = Object.assign({}, data);
      previousKpiExtras = Object.assign({}, extras);
    } else {
      $("overview-kpis").innerHTML =
        kpi(t("kpis.active_tests"), data.active_tests ?? 0, "info") +
        kpi(t("kpis.completed"), data.completed_tests ?? 0) +
        kpi(t("kpis.total_queries"), data.total_queries ?? 0) +
        kpi(t("kpis.errors"), data.error_count ?? 0, errCls) +
        kpi(t("kpis.success_pct"), ratio, parseFloat(ratio) > 90 ? "ok" : "warn") +
        kpi(t("kpis.nxdomain"), data.nxdomain_count ?? 0);
    }
    bindKpiDrilldowns();
  }

  function renderCompareDeltas(compareData) {
    const deltas = (compareData && compareData.deltas) || {};
    const ov = deltas.overview || {};
    const lat = deltas.dns_latency || {};
    const err = deltas.errors || {};
    const garbage = deltas.garbage || {};
    const cache = deltas.cache || {};
    const load = deltas.load || {};
    const rankings = deltas.rankings || {};
    $("compare-deltas").innerHTML =
      deltaKpi(t("compare.delta_errors"), ov.error_count || err.total_errors, true) +
      deltaKpi(t("compare.delta_success_pct"), ov.success_ratio, false) +
      deltaKpi(t("compare.delta_error_qps"), err.error_qps, true) +
      deltaKpi(t("compare.delta_p50"), lat.p50, true) +
      deltaKpi(t("compare.delta_p95"), lat.p95, true) +
      deltaKpi(t("compare.delta_p99"), lat.p99, true) +
      deltaKpi(t("compare.delta_garbage_pct"), garbage.garbage_ratio, true) +
      deltaKpi(t("compare.delta_cache_hit_pct"), cache.hit_ratio, false) +
      deltaKpi(t("compare.delta_error_rate"), load.error_rate, true) +
      deltaKpi(t("compare.delta_qps"), load.actual_qps, false) +
      deltaKpi(t("compare.delta_top_domain_err"), rankings.domains_top_error_rate, true);
    renderErrorMatrixDelta(deltas.resolver_error_matrix);
  }

  function renderErrorMatrixDelta(matrixDeltas) {
    const wrap = $("error-matrix-wrap");
    if (!matrixDeltas || !Object.keys(matrixDeltas).length) {
      wrap.classList.add("hidden");
      return;
    }
    wrap.classList.remove("hidden");
    const classes = new Set();
    Object.values(matrixDeltas).forEach((row) => Object.keys(row).forEach((c) => classes.add(c)));
    const clsList = Array.from(classes).sort();
    $("error-matrix-head").innerHTML = "<th>" + t("mtr.resolver") + "</th>" + clsList.map((c) => "<th>" + c + "</th>").join("");
    $("error-matrix-body").innerHTML = Object.keys(matrixDeltas).sort().map((resolver) => {
      const row = matrixDeltas[resolver];
      return "<tr><td>" + resolver + "</td>" + clsList.map((c) => {
        const d = row[c];
        if (!d || d.absolute === null) return "<td>—</td>";
        const sign = d.absolute > 0 ? "+" : "";
        const cls = d.absolute < 0 ? "delta-good" : d.absolute > 0 ? "delta-bad" : "";
        return '<td class="' + cls + '">' + sign + d.absolute + "</td>";
      }).join("") + "</tr>";
    }).join("");
  }

  function renderLatency(data, compareData) {
    setPanelBadge("latency", data);
    if ((data.sample_count || 0) === 0) {
      panelState("state-latency", t("states.no_latency"), "empty");
    } else {
      panelState("state-latency", "");
    }

    $("latency-kpis").innerHTML =
      kpi(t("kpis.p50"), fmtMs(data.p50 || 0)) +
      kpi(t("kpis.p95"), fmtMs(data.p95 || 0), "warn") +
      kpi(t("kpis.p99"), fmtMs(data.p99 || 0), "crit") +
      kpi(t("kpis.samples"), data.sample_count ?? 0);

    const buckets = data.time_buckets || [];
    const c = chartColors();
    if (viewMode === "compare" && compareData) {
      const base = compareData.time_series_overlay.baseline || [];
      const comp = compareData.time_series_overlay.comparison || [];
      ensureChart("chart-latency-ts", "line", {
        labels: base.map((b) => (b.timestamp || "").slice(11, 19)),
        datasets: [
          { label: t("charts.baseline_p95"), data: base.map((b) => b.p95), borderColor: c.palette[0], tension: 0.2 },
          { label: t("charts.comparison_p95"), data: comp.map((b) => b.p95), borderColor: c.crit, tension: 0.2 },
        ],
      });
    } else {
      const datasets = [];
      if (latencySeriesVisible.p50) {
        datasets.push({ label: "p50", data: buckets.map((b) => b.p50), borderColor: c.palette[0], tension: 0.2 });
      }
      if (latencySeriesVisible.p95) {
        datasets.push({ label: "p95", data: buckets.map((b) => b.p95), borderColor: c.warn, tension: 0.2 });
      }
      if (latencySeriesVisible.p99) {
        datasets.push({ label: "p99", data: buckets.map((b) => b.p99), borderColor: c.crit, tension: 0.2 });
      }
      ensureChart("chart-latency-ts", "line", {
        labels: buckets.map((b) => (b.timestamp || "").slice(11, 19)),
        datasets: datasets,
      }, {
        plugins: {
          legend: {
            labels: {
              color: c.text,
              filter: function (item, chart) {
                return chart.datasets.length <= 6;
              },
            },
          },
        },
      });
      if (viewMode === "live") previousLatency = { p50: data.p50, p95: data.p95, p99: data.p99 };
    }

    const modes = data.by_resolve_mode || {};
    ensureChart("chart-latency-mode", "bar", {
      labels: Object.keys(modes),
      datasets: [{ label: t("charts.p95_ms"), data: Object.values(modes).map((m) => m.p95), backgroundColor: c.palette[0] }],
    });
  }

  function renderEdns(data) {
    setPanelBadge("edns", data);
    const note = t("charts.edns_note");
    $("edns-note").textContent = note;
    const levels = data.levels || [];
    const total = levels.reduce((s, l) => s + (l.queries || 0), 0);
    if (total === 0) {
      panelState("state-edns", note || t("states.no_edns"), "empty");
    } else {
      panelState("state-edns", "");
    }
    ensureChart("chart-edns", "bar", {
      labels: levels.map((l) => l.level),
      datasets: [
        { label: t("charts.queries"), data: levels.map((l) => l.queries), backgroundColor: chartColors().palette[0] },
        { label: t("charts.errors"), data: levels.map((l) => l.errors), backgroundColor: chartColors().crit },
      ],
    });
  }

  function renderErrorMatrix(data) {
    const matrix = data.resolver_error_matrix || {};
    const keys = Object.keys(matrix);
    const wrap = $("error-matrix-wrap");
    if (!keys.length || viewMode === "compare") return;
    wrap.classList.remove("hidden");
    const classes = new Set();
    keys.forEach((r) => Object.keys(matrix[r]).forEach((c) => classes.add(c)));
    const clsList = Array.from(classes).sort();
    $("error-matrix-head").innerHTML = "<th>" + t("mtr.resolver") + "</th>" + clsList.map((c) => "<th>" + c + "</th>").join("");
    $("error-matrix-body").innerHTML = keys.sort().map((resolver) => {
      const row = matrix[resolver];
      return "<tr><td>" + resolver + "</td>" + clsList.map((c) => {
        const v = row[c] || 0;
        const heat = v > 10 ? "heat-high" : v > 0 ? "heat-low" : "";
        return '<td class="' + heat + '">' + v + "</td>";
      }).join("") + "</tr>";
    }).join("");
  }

  function renderErrors(data, compareData) {
    setPanelBadge("errors", data);
    if (viewMode === "compare" && compareData) {
      const base = (compareData.baseline && compareData.baseline.errors) || {};
      const comp = (compareData.comparison && compareData.comparison.errors) || data || {};
      const total = comp.total_errors || 0;
      if (total === 0 && (base.total_errors || 0) === 0) {
        panelState("state-errors", t("states.no_errors_compare_good"), "empty");
      } else {
        panelState("state-errors", "");
      }
      const baseClass = base.by_error_class || {};
      const compClass = comp.by_error_class || {};
      const labels = Array.from(new Set([...Object.keys(baseClass), ...Object.keys(compClass)])).sort();
      ensureChart("chart-errors-class", "bar", {
        labels: labels,
        datasets: [
          { label: t("charts.baseline"), data: labels.map((l) => baseClass[l] || 0), backgroundColor: chartColors().palette[0] },
          { label: t("charts.comparison"), data: labels.map((l) => compClass[l] || 0), backgroundColor: chartColors().crit },
        ],
      });
      const baseRes = base.by_resolver || {};
      const compRes = comp.by_resolver || {};
      const resLabels = Array.from(new Set([...Object.keys(baseRes), ...Object.keys(compRes)])).sort();
      ensureChart("chart-errors-resolver", "bar", {
        labels: resLabels,
        datasets: [
          { label: t("charts.baseline"), data: resLabels.map((l) => baseRes[l] || 0), backgroundColor: chartColors().palette[0] },
          { label: t("charts.comparison"), data: resLabels.map((l) => compRes[l] || 0), backgroundColor: chartColors().crit },
        ],
      });
      return;
    }

    if ((data.total_errors || 0) === 0) {
      panelState("state-errors", t("states.no_errors_good"), "empty");
      $("error-matrix-wrap").classList.add("hidden");
    } else {
      panelState("state-errors", "");
      renderErrorMatrix(data);
    }
    const byClass = data.by_error_class || {};
    ensureChart("chart-errors-class", "doughnut", {
      labels: Object.keys(byClass),
      datasets: [{ data: Object.values(byClass), backgroundColor: chartColors().palette }],
    });
    const byRes = data.by_resolver || {};
    ensureChart("chart-errors-resolver", "bar", {
      labels: Object.keys(byRes),
      datasets: [{ label: t("charts.errors"), data: Object.values(byRes), backgroundColor: chartColors().crit }],
    });
  }

  function renderGarbage(data) {
    setPanelBadge("garbage", data);
    const nc = data.noise_counts || {};
    const total = Object.values(nc).reduce((a, b) => a + b, 0);
    if (total === 0 && (data.useful_vs_garbage_ratio || {}).useful === 0) {
      panelState("state-garbage", t("states.no_garbage"), "empty");
    } else {
      panelState("state-garbage", "");
    }
    ensureChart("chart-garbage", "bar", {
      labels: Object.keys(nc),
      datasets: [{ label: t("charts.count"), data: Object.values(nc), backgroundColor: chartColors().warn }],
    });
    const ratio = data.useful_vs_garbage_ratio || {};
    $("garbage-summary").innerHTML =
      "<p>" + t("garbage.useful") + ": <strong>" + (ratio.useful || 0) + "</strong> · " + t("garbage.garbage") + ": <strong>" +
      (ratio.garbage || 0) + "</strong> · " + t("garbage.garbage_ratio") + ": <strong>" +
      fmtPct(ratio.garbage_ratio || 0, 1) + "</strong></p>";
    const noisy = data.top_noisy_domains || {};
    const entries = Object.entries(noisy).slice(0, 10);
    $("top-noisy-domains").innerHTML = entries.length
      ? "<h4>" + t("garbage.top_noisy_domains") + "</h4><ul>" + entries.map(([d, n]) =>
          '<li><button type="button" class="link-btn" data-domain="' + d + '">' + d + "</button> — " + n + "</li>"
        ).join("") + "</ul>"
      : "";
    $("top-noisy-domains").querySelectorAll(".link-btn").forEach((btn) => {
      btn.addEventListener("click", () => drilldownDomain(btn.dataset.domain));
    });
  }

  function renderCache(data) {
    setPanelBadge("cache", data);
    $("cache-disclaimer").textContent = t("states.cache_disclaimer");
    const hitPct = fmtPct(data.hit_ratio || 0, 2);
    panelState("state-cache", "");
    $("cache-kpis").innerHTML =
      kpi(t("kpis.possible_hits"), data.possible_cache_hits ?? 0, "info") +
      kpi(t("kpis.hit_ratio"), hitPct, "warn") +
      kpi(t("kpis.repeat_keys"), data.repeat_query_keys ?? 0);
  }

  function filterRecordsClient(rows) {
    let filtered = rows;
    if (clientSearch) {
      const q = clientSearch.toLowerCase();
      filtered = filtered.filter((r) => r.fqdn.toLowerCase().indexOf(q) >= 0);
    }
    if (clientStatusFilter) {
      if (clientStatusFilter === "noisy") {
        filtered = filtered.filter((r) => r.status === "error" || (r.errors || 0) > 0);
      } else {
        filtered = filtered.filter((r) => r.status === clientStatusFilter);
      }
    }
    return filtered;
  }

  function sortRecords(rows) {
    const key = recordsSort.key;
    const dir = recordsSort.dir;
    return rows.slice().sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }

  function highlightFqdn(fqdn) {
    if (!clientSearch) return fqdn;
    const idx = fqdn.toLowerCase().indexOf(clientSearch.toLowerCase());
    if (idx < 0) return fqdn;
    return fqdn.slice(0, idx) + '<mark class="search-hit">' + fqdn.slice(idx, idx + clientSearch.length) + "</mark>" + fqdn.slice(idx + clientSearch.length);
  }

  function drilldownDomain(domain) {
    clientSearch = domain;
    $("filter-search").value = domain;
    scrollToPanel("panel-records");
    renderRecords({ records: recordsRows });
    renderFilterChips(lastEnvelope, null);
  }

  async function openEventsModal(record) {
    const testId = $("filter-test-id").value;
    const p = new URLSearchParams(queryParams().replace(/^\?/, ""));
    if (record) p.set("record", record);
    p.set("limit", "50");
    const res = await fetch(API + "/events?" + p.toString());
    if (!res.ok) return;
    const data = await res.json();
    $("events-modal-title").textContent = record
      ? t("modals.recent_events_for", { record: record })
      : t("modals.recent_events");
    const tbody = document.querySelector("#events-table tbody");
    tbody.innerHTML = (data.events || []).map((e) =>
      "<tr><td>" + (e.timestamp || "").slice(11, 19) + "</td><td>" + e.record + "</td><td>" +
      e.query_type + "</td><td>" + e.resolve_mode + "</td><td class=\"status-" + e.outcome + "\">" +
      e.outcome + "</td><td>" + e.latency_ms + " ms</td></tr>"
    ).join("");
    $("events-modal").showModal();
  }

  async function openDiagnosisModal(testId) {
    const base = cfg.basePath.replace(/\/dns-debug$/, "") || "";
    const url = (base || "") + "/tests/" + encodeURIComponent(testId) + "/diagnosis";
    const res = await fetch(url);
    if (!res.ok) {
      $("diagnosis-json").textContent = t("states.diagnosis_failed", { status: res.status });
    } else {
      const data = await res.json();
      $("diagnosis-json").textContent = JSON.stringify(data, null, 2);
    }
    $("diagnosis-modal-title").textContent = t("modals.diagnosis_for", { testId: testId });
    $("diagnosis-modal").showModal();
  }

  function renderRecords(data) {
    setPanelBadge("records", data);
    recordsRows = data.records || [];
    const filtered = filterRecordsClient(recordsRows);
    const sorted = sortRecords(filtered);
    const tbody = document.querySelector("#records-table tbody");
    if (!sorted.length) {
      panelState("state-records", clientSearch || clientStatusFilter
        ? t("states.no_records_search")
        : t("states.no_records_filters"), "empty");
      tbody.innerHTML = "";
      return;
    }
    panelState("state-records", "");
    const testId = $("filter-test-id").value;
    tbody.innerHTML = sorted.map((r) =>
      "<tr data-fqdn=\"" + r.fqdn + "\"><td>" + highlightFqdn(r.fqdn) + "</td><td>" + r.query_type + "</td><td>" + r.resolve_mode +
      '</td><td class="status-' + r.status + '">' + r.status + "</td><td>" + r.queries +
      "</td><td>" + r.errors + "</td><td>" + r.avg_latency_ms +       '</td><td class="row-actions">' +
      '<button type="button" class="link-btn btn-events" data-record="' + r.fqdn + '">' + t("records.events") + '</button>' +
      (testId ? ' <button type="button" class="link-btn btn-diagnosis" data-test="' + testId + '">' + t("records.diagnosis") + '</button>' : "") +
      "</td></tr>"
    ).join("");
    tbody.querySelectorAll("tr").forEach((tr) => {
      tr.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        drilldownDomain(tr.dataset.fqdn);
      });
    });
    tbody.querySelectorAll(".btn-events").forEach((btn) => {
      btn.addEventListener("click", (e) => { e.stopPropagation(); openEventsModal(btn.dataset.record); });
    });
    tbody.querySelectorAll(".btn-diagnosis").forEach((btn) => {
      btn.addEventListener("click", (e) => { e.stopPropagation(); openDiagnosisModal(btn.dataset.test); });
    });
    document.querySelectorAll("#records-table th.sortable").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.sort === recordsSort.key);
      th.setAttribute("aria-sort", th.dataset.sort === recordsSort.key ? (recordsSort.dir > 0 ? "ascending" : "descending") : "none");
    });
  }

  function renderLoad(data) {
    setPanelBadge("load", data);
    if ((data.actual_qps || 0) === 0 && (data.configured_rps || 0) === 0) {
      panelState("state-load", t("states.no_load"), "empty");
    } else {
      panelState("state-load", "");
    }
    $("load-kpis").innerHTML =
      kpi(t("kpis.configured_rps"), data.configured_rps ?? 0) +
      kpi(t("kpis.actual_qps"), data.actual_qps ?? 0, "info") +
      kpi(t("kpis.saturation"), fmtPct(data.saturation_ratio || 0, 0), (data.saturation_ratio || 0) > 0.8 ? "crit" : "ok") +
      kpi(t("kpis.success_rate"), fmtPct(data.success_rate || 0, 1)) +
      kpi(t("kpis.error_rate"), fmtPct(data.error_rate || 0, 1), "warn");

    const ts = data.time_series || [];
    ensureChart("chart-load", "line", {
      labels: ts.map((b) => (b.timestamp || "").slice(11, 19)),
      datasets: [
        { label: t("charts.error_rate"), data: ts.map((b) => b.error_rate), borderColor: chartColors().crit, yAxisID: "y" },
        { label: t("charts.count"), data: ts.map((b) => b.count), borderColor: chartColors().palette[0], yAxisID: "y1" },
      ],
    }, {
      scales: {
        y: { position: "left", ticks: { color: chartColors().text } },
        y1: { position: "right", grid: { drawOnChartArea: false }, ticks: { color: chartColors().text } },
        x: { ticks: { color: chartColors().text }, grid: { color: chartColors().grid } },
      },
    });
  }

  function verdictClass(v) {
    if (!v || v === "ok" || v === "unknown") return "verdict-ok";
    if (v === "unstable_path") return "verdict-warn";
    return "verdict-crit";
  }

  function renderMtr(data) {
    setPanelBadge("mtr", data);
    const el = $("mtr-verdict");
    const verdict = data.verdict || "unknown";
    el.className = "verdict-card " + verdictClass(verdict);
    if (!data.mtr_enabled) {
      panelState("state-mtr", t("mtr.not_enabled"), "empty");
      el.textContent = t("mtr.not_enabled_short");
      document.querySelector("#mtr-table tbody").innerHTML = "";
      return;
    }
    const hops = (data.latest && data.latest.hops) ? data.latest.hops : [];
    if (!hops.length) {
      panelState("state-mtr", t("mtr.no_runs"), "empty");
    } else {
      panelState("state-mtr", "");
    }
    let verdictText = t("mtr.verdict") + ": " + verdict + (data.latest ? " — " + data.latest.service_name + ":" + data.latest.port : "");
    if (data.truncated_runs > 0) {
      verdictText += " · " + (data.truncated_reason ? data.truncated_reason : t("mtr.truncated"));
    }
    el.textContent = verdictText;
    const tbody = document.querySelector("#mtr-table tbody");
    tbody.innerHTML = hops.map((h) =>
      "<tr" + (h.loss_pct >= 5 ? ' class="status-error"' : "") + "><td>" + h.hop + "</td><td>" + h.host +
      "</td><td>" + h.loss_pct + "</td><td>" + (h.avg_ms ?? "—") + "</td><td>" + (h.best_ms ?? "—") +
      "</td><td>" + (h.worst_ms ?? "—") + "</td></tr>"
    ).join("");
  }

  function renderRankings(data) {
    lastRankingsData = data;
    setPanelBadge("rankings", data);
    const hasAny = ["resolvers", "domains", "query_types", "mtr_targets"].some((k) => (data[k] || []).length);
    if (!hasAny) {
      panelState("state-rankings", t("states.no_rankings"), "empty");
    } else {
      panelState("state-rankings", "");
    }
    function block(title, items, keyField) {
      const rows = (items || []).slice(0, 8).map((i) => {
        const key = i[keyField || "key"];
        const highlight = clientSearch && key.toLowerCase().indexOf(clientSearch.toLowerCase()) >= 0 ? " ranking-hit" : "";
        return '<li class="ranking-row' + highlight + '" data-domain="' + key + '"><span>' + key + '</span><span>' +
          ((i.error_rate || 0) * 100).toFixed(1) + "% · " + (i.avg_latency_ms || 0) + " ms</span></li>";
      }).join("");
      return '<div class="ranking-block"><h4>' + title + "</h4><ul>" + (rows || "<li>—</li>") + "</ul></div>";
    }
    $("rankings-grid").innerHTML =
      block(t("rankings.resolvers"), data.resolvers) +
      block(t("rankings.domains"), data.domains) +
      block(t("rankings.query_types"), data.query_types) +
      block(t("rankings.mtr_targets"), data.mtr_targets, "target");
    $("rankings-grid").querySelectorAll(".ranking-row[data-domain]").forEach((li) => {
      li.addEventListener("click", () => drilldownDomain(li.dataset.domain));
    });
  }

  function buildSnapshotOptions(list, emptyLabel) {
    const grouped = {};
    list.forEach((s) => {
      const name = s.test_name || "unknown";
      if (!grouped[name]) grouped[name] = [];
      grouped[name].push(s);
    });
    let html = '<option value="">' + emptyLabel + "</option>";
    Object.keys(grouped).sort().forEach((name) => {
      html += '<optgroup label="' + name + '">';
      grouped[name].forEach((s) => {
        html += '<option value="' + s.snapshot_id + '">' + s.created_at + " · " + s.snapshot_id + "</option>";
      });
      html += "</optgroup>";
    });
    return html;
  }

  async function loadSnapshots() {
    try {
      const res = await fetch(API + "/snapshots");
      if (!res.ok) return;
      const data = await res.json();
      const list = data.snapshots || [];
      const opts = buildSnapshotOptions(list, t("filters.live_buffer"));
      const snapOpts = buildSnapshotOptions(list, "—");
      ["filter-snapshot", "baseline-snapshot", "compare-snapshot"].forEach((id, i) => {
        const el = $(id);
        const cur = el.value;
        el.innerHTML = i === 0 ? opts : snapOpts;
        el.value = cur;
      });
      if (!list.length && viewMode === "historical") {
        showBanner("info-banner", t("history.no_snapshots"));
      } else if (data.retention_count && list.length >= data.retention_count) {
        showBanner("warn-banner", t("history.warnings.snapshot_retention_at_limit", { limit: data.retention_count }));
      }
    } catch (e) {
      console.warn("snapshots list failed", e);
    }
  }

  async function refreshAll() {
    if (loading) return;
    setLoading(true);
    showBanner("error-banner", "");
    try {
      if (viewMode === "compare") {
        if (!compareScopeSelected()) {
          showBanner("info-banner", t("compare.select_scopes"));
          resetPanels(t("compare.choose_scopes"));
          renderFilterChips(lastEnvelope, null);
          setLoading(false);
          firstLoad = false;
          return;
        }
        const compareData = await fetchJson("/compare", true);
        const comparison = compareData.comparison || {};
        const comparisonOverview = comparison.overview || {};
        renderOverview(comparisonOverview, compareData, comparison.dns_latency);
        renderCompareDeltas(compareData);
        renderLatency(comparison.dns_latency || {}, compareData);
        renderEdns(await fetchComparisonJson("/edns"));
        renderErrors(comparison.errors || {}, compareData);
        renderGarbage(comparison.garbage || {});
        renderCache(comparison.cache || await fetchComparisonJson("/cache"));
        renderRecords(await fetchComparisonJson("/records"));
        renderLoad(comparison.load || await fetchComparisonJson("/load"));
        renderMtr(await fetchComparisonJson("/mtr"));
        renderRankings(comparison.rankings || await fetchComparisonJson("/rankings"));
        const suffix = t("compare.suffix");
        $("last-update").textContent = (comparisonOverview.last_update ? t("common.updated") + " " + comparisonOverview.last_update : "—") + suffix;
      } else if (viewMode === "historical" && !historicalScopeSelected()) {
        showBanner("info-banner", t("history.select_scope"));
        resetPanels(t("history.no_scope"));
        renderFilterChips(lastEnvelope, null);
      } else {
        const [overview, latency, edns, errors, garbage, cache, records, load, mtr, rankings] = await Promise.all([
          fetchJson("/overview"),
          fetchJson("/dns-latency"),
          fetchJson("/edns"),
          fetchJson("/errors"),
          fetchJson("/garbage"),
          fetchJson("/cache"),
          fetchJson("/records"),
          fetchJson("/load"),
          fetchJson("/mtr"),
          fetchJson("/rankings"),
        ]);
        renderOverview(overview, null, latency);
        renderLatency(latency);
        renderEdns(edns);
        renderErrors(errors);
        renderGarbage(garbage);
        renderCache(cache);
        renderRecords(records);
        renderLoad(load);
        renderMtr(mtr);
        renderRankings(rankings);
      }
    } catch (e) {
      console.error("DNS Debug UI refresh failed", e);
      $("health-badge").textContent = t("common.level.error");
      $("health-badge").className = "badge badge-crit";
      showBanner("error-banner", t("states.load_failed") + " " + e.message);
    } finally {
      setLoading(false);
      firstLoad = false;
    }
  }

  function bindFilters() {
    const changeIds = [
      "filter-test-id", "filter-resolve-mode", "filter-query-type", "filter-from", "filter-to", "filter-snapshot",
      "baseline-from", "baseline-to", "compare-from", "compare-to", "baseline-snapshot", "compare-snapshot",
      "baseline-test-id", "compare-test-id", "baseline-resolve-mode", "compare-resolve-mode", "live-time-preset"
    ];
    changeIds.forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.addEventListener("change", refreshAll);
    });

    $("filter-search").addEventListener("input", () => {
      clientSearch = $("filter-search").value.trim();
      renderRecords({ records: recordsRows });
      if (lastRankingsData) renderRankings(lastRankingsData);
      renderFilterChips(lastEnvelope, null);
    });
    $("filter-status").addEventListener("change", () => {
      clientStatusFilter = $("filter-status").value;
      renderRecords({ records: recordsRows });
      renderFilterChips(lastEnvelope, null);
    });

    document.querySelectorAll(".mode-btn").forEach((btn) => {
      btn.setAttribute("tabindex", btn.classList.contains("active") ? "0" : "-1");
      btn.addEventListener("click", () => {
        viewMode = btn.dataset.mode || "live";
        if (viewMode === "live") autoRefreshEnabled = true;
        setModeUi();
        refreshAll();
      });
    });

    const modeGroup = document.querySelector(".mode-toggle");
    if (modeGroup) {
      modeGroup.addEventListener("keydown", (e) => {
        const btns = Array.from(document.querySelectorAll(".mode-btn"));
        const idx = btns.findIndex((b) => b.classList.contains("active"));
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          e.preventDefault();
          const next = e.key === "ArrowRight"
            ? btns[(idx + 1) % btns.length]
            : btns[(idx - 1 + btns.length) % btns.length];
          viewMode = next.dataset.mode || "live";
          if (viewMode === "live") autoRefreshEnabled = true;
          setModeUi();
          refreshAll();
        }
      });
    }

    $("refresh-btn").addEventListener("click", refreshAll);
    $("reset-filters-btn").addEventListener("click", resetAllFilters);
    $("error-retry").addEventListener("click", refreshAll);
    $("auto-refresh-toggle").addEventListener("click", () => {
      autoRefreshEnabled = !autoRefreshEnabled;
      updateAutoRefreshButton();
      setupRefreshTimer();
    });
    $("more-filters-btn").addEventListener("click", () => {
      $("secondary-controls").classList.toggle("hidden");
    });

    document.querySelectorAll("#latency-series-toggles input").forEach((cb) => {
      cb.addEventListener("change", () => {
        latencySeriesVisible[cb.dataset.series] = cb.checked;
        refreshAll();
      });
    });

    document.querySelectorAll("#records-table th.sortable").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (recordsSort.key === key) recordsSort.dir *= -1;
        else {
          recordsSort.key = key;
          recordsSort.dir = key === "fqdn" ? 1 : -1;
        }
        renderRecords({ records: recordsRows });
      });
    });

    $("events-modal-close").addEventListener("click", () => $("events-modal").close());
    $("diagnosis-modal-close").addEventListener("click", () => $("diagnosis-modal").close());

    document.querySelectorAll(".sub-nav-link").forEach((link) => {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        const id = link.getAttribute("href").slice(1);
        scrollToPanel(id);
      });
    });
  }

  function initTopBarHeight() {
    const topBar = document.querySelector(".top-bar");
    if (!topBar) return;
    const sync = () => {
      document.documentElement.style.setProperty("--top-bar-height", topBar.offsetHeight + "px");
    };
    sync();
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(sync).observe(topBar);
    } else {
      window.addEventListener("resize", sync);
    }
  }

  function initSubNavSpy() {
    const zones = Array.from(document.querySelectorAll(".dashboard-zone"));
    const links = document.querySelectorAll(".sub-nav-link");
    if (!zones.length || !links.length) return;
    const visible = new Map();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            visible.set(entry.target.id, entry.intersectionRatio);
          } else {
            visible.delete(entry.target.id);
          }
        });
        let bestId = null;
        let bestRatio = 0;
        visible.forEach((ratio, id) => {
          if (ratio > bestRatio) {
            bestRatio = ratio;
            bestId = id;
          }
        });
        if (!bestId) return;
        links.forEach((link) => {
          link.classList.toggle("active", link.getAttribute("href").slice(1) === bestId);
        });
      },
      { rootMargin: "-10% 0px -55% 0px", threshold: [0, 0.1, 0.25, 0.5, 0.75, 1] }
    );
    zones.forEach((zone) => observer.observe(zone));
  }

  narrowChart.addEventListener("change", () => {
    const pos = chartLegendPosition();
    Object.values(charts).forEach((ch) => {
      if (!ch || !ch.options.plugins || !ch.options.plugins.legend) return;
      ch.options.plugins.legend.position = pos;
      ch.update("none");
    });
  });

  themeInit();
  if (I18N) {
    const i18nCfg = Object.assign({}, cfg.i18n || {}, { basePath: cfg.basePath });
    if (!i18nCfg.fallbackMessages && cfg.i18n && cfg.i18n.fallbackMessages) {
      i18nCfg.fallbackMessages = cfg.i18n.fallbackMessages;
    }
    I18N.onLangChange(function () {
      updateAutoRefreshButton();
      setModeUi();
      if (I18N.applyDom) I18N.applyDom();
      refreshAll();
    });
    const boot = I18N.bootstrap ? I18N.bootstrap(i18nCfg) : Promise.resolve(I18N.init(i18nCfg));
    boot.then(function () {
      bindFilters();
      initTopBarHeight();
      initSubNavSpy();
      setModeUi();
      updateAutoRefreshButton();
      loadSnapshots();
      refreshAll();
    });
  } else {
    bindFilters();
    initTopBarHeight();
    initSubNavSpy();
    setModeUi();
    updateAutoRefreshButton();
    loadSnapshots();
    refreshAll();
  }
})();
