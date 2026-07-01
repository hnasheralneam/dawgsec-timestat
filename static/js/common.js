/*
 * Shared helpers used across TimeStat page templates.
 *
 * Loaded once from base.html before any per-page <script> blocks, so the
 * functions below are attached to `window` and can be called directly
 * (e.g. `postJson(...)`) from inline page scripts without redeclaring them.
 */
(function () {
  "use strict";

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatDuration(total) {
    const sec = Math.max(0, Math.floor(total || 0));
    const h = String(Math.floor(sec / 3600)).padStart(2, "0");
    const m = String(Math.floor((sec % 3600) / 60)).padStart(2, "0");
    const s = String(sec % 60).padStart(2, "0");
    return `${h}:${m}:${s}`;
  }

  function formatHours(seconds) {
    return (seconds / 3600).toFixed(2);
  }

  function tsToLocal(ts) {
    if (!ts) return "-";
    return new Date(ts * 1000).toLocaleString();
  }

  async function postJson(url, body = {}) {
    const csrfToken = window.getCsrfToken ? window.getCsrfToken() : "";
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken
      },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Request failed");
    return data;
  }

  /**
   * Creates a Chart.js instance on first use, and on subsequent calls with
   * the same `chart` reference just updates its data/options in place
   * (chart.update()) instead of destroying and recreating it. This avoids
   * the visible flash/flicker that comes from tearing down and rebuilding
   * the canvas on every poll.
   *
   * @param {Chart|null} chart - existing Chart.js instance, or null/undefined
   *   if this is the first render.
   * @param {HTMLCanvasElement|CanvasRenderingContext2D} ctx - canvas element
   *   (or context) to render into when creating a new chart.
   * @param {object} config - standard Chart.js config ({ type, data, options }).
   * @returns {Chart} the live (created or updated) Chart.js instance.
   */
  function renderOrUpdateChart(chart, ctx, config) {
    if (chart) {
      chart.data = config.data;
      if (config.options) chart.options = config.options;
      if (config.type) chart.config.type = config.type;
      chart.update();
      return chart;
    }
    return new Chart(ctx, config);
  }

  window.escapeHtml = escapeHtml;
  window.formatDuration = formatDuration;
  window.formatHours = formatHours;
  window.tsToLocal = tsToLocal;
  window.postJson = postJson;
  window.renderOrUpdateChart = renderOrUpdateChart;
})();
