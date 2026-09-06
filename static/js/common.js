/*
 * Shared helpers used across TimeStat page templates.
 *
 * Loaded once from base.html before any per-page <script> blocks, so the
 * functions below are attached to `window` and can be called directly
 * (e.g. `postJson(...)`) from inline page scripts without redeclaring them.
 *
 * DOM-dependent setup (CSRF form injection, theme toggle, tooltip, chart
 * loader) runs once on DOMContentLoaded.
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
          "X-CSRF-Token": csrfToken,
          "X-TimeStat-Client-State": "1"
        },
      body: JSON.stringify(body)
    });
    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      // An unhandled server error (or a proxy/gateway error page) returns
      // HTML, not JSON. Parsing that as JSON throws a cryptic SyntaxError -
      // surface a plain, actionable message instead.
      throw new Error(`Request failed (HTTP ${res.status})`);
    }
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
    // Charts are optional. A failed lazy-load must not turn a session action
    // or data refresh into a failed operation.
    if (typeof window.Chart === "undefined" || !ctx) return chart || null;
    if (chart) {
      const oldLabels = chart.data.labels || [];
      const newLabels = config.data.labels || [];
      const isSameLabels = oldLabels.length === newLabels.length &&
        oldLabels.every((l, i) => l === newLabels[i]);

      const oldDsets = chart.data.datasets || [];
      const newDsets = config.data.datasets || [];
      const isSameData = oldDsets.length === newDsets.length &&
        oldDsets.every((d, i) => {
          if (!newDsets[i]) return false;
          const oldVal = d.data || [];
          const newVal = newDsets[i].data || [];
          return oldVal.length === newVal.length &&
            oldVal.every((v, idx) => v === newVal[idx]);
        });

      if (isSameLabels && isSameData && !config.force) {
        return chart;
      }

      chart.data = config.data;
      if (config.options) chart.options = config.options;
      if (config.type) chart.config.type = config.type;
      chart.update('none');
      return chart;
    }
    return new Chart(ctx, config);
  }

  // Lazy-loads Chart.js only when a page actually needs to render charts.
  // The ~200KB bundle is self-hosted (static/vendor/) so it loads from this
  // origin (no third-party roundtrip) and is precached/staled-while-
  // revalidated by the service worker for offline use. Page scripts should
  // `await window.ensureCharts()` before calling any chart-rendering code;
  // repeated calls share a single script load.
  let chartLoadPromise = null;
  function ensureCharts() {
    if (typeof window.Chart !== "undefined") return Promise.resolve();
    if (chartLoadPromise) return chartLoadPromise;
    chartLoadPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      // Version the URL with the server-computed static content hash (set in
      // base.html) so a deploy busts the browser/proxy caches for the bundle.
      s.src = "/static/vendor/chart.umd.min.js" +
        (window.TIMESTAT_STATIC_V ? `?v=${encodeURIComponent(window.TIMESTAT_STATIC_V)}` : "");
      s.async = true;
      s.onload = () => resolve();
      s.onerror = () => {
        chartLoadPromise = null;
        reject(new Error("Could not load charts"));
      };
      document.head.appendChild(s);
    });
    return chartLoadPromise;
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  function cssRgb(varName) {
    return `rgb(${getComputedStyle(document.documentElement).getPropertyValue(varName).trim()})`;
  }

  function themeColors() {
    return [
      "--gb-blue-rgb", "--gb-green-rgb", "--gb-yellow-rgb", "--gb-red-rgb", "--gb-purple-rgb",
      "--gb-aqua-rgb", "--gb-fg-rgb", "--gb-bg2-rgb", "--gb-shadow-rgb", "--gb-highlight-rgb"
    ].map(cssRgb);
  }

  window.escapeHtml = escapeHtml;
  window.formatDuration = formatDuration;
  window.formatHours = formatHours;
  window.tsToLocal = tsToLocal;
  window.postJson = postJson;
  window.renderOrUpdateChart = renderOrUpdateChart;
  window.ensureCharts = ensureCharts;
  window.getCsrfToken = getCsrfToken;
  window.cssRgb = cssRgb;
  window.themeColors = themeColors;
  window.MEDALS = ["🥇", "🥈", "🥉"];

  // The confirm modal is a page-wide singleton, so only one call can own it
  // at a time. If a second call comes in while one is still open, resolve
  // the first one as cancelled (and unwind its listener) before opening the
  // new one - otherwise the first call's promise hangs forever and its
  // keydown listener leaks.
  let activeConfirmCleanup = null;
  window.showAppConfirm = function (message) {
    if (activeConfirmCleanup) activeConfirmCleanup(false);
    return new Promise((resolve) => {
      const modal = document.getElementById("appConfirmModal");
      document.getElementById("appConfirmMessage").textContent = message;
      modal.classList.remove("hidden");
      modal.classList.add("flex");
      const ok = document.getElementById("appConfirmOk");
      const cancel = document.getElementById("appConfirmCancel");
      ok.focus();
      function onKeydown(e) {
        if (e.key === "Escape") {
          e.preventDefault();
          cleanup(false);
        } else if (e.key === "Tab") {
          // Trap focus within the two modal buttons.
          const focusables = [cancel, ok];
          const idx = focusables.indexOf(document.activeElement);
          const dir = e.shiftKey ? -1 : 1;
          const next = focusables[(idx + dir + focusables.length) % focusables.length];
          e.preventDefault();
          (idx === -1 ? focusables[0] : next).focus();
        }
      }
      function cleanup(result) {
        modal.classList.add("hidden");
        modal.classList.remove("flex");
        ok.onclick = null;
        cancel.onclick = null;
        modal.removeEventListener("keydown", onKeydown);
        activeConfirmCleanup = null;
        resolve(result);
      }
      activeConfirmCleanup = cleanup;
      modal.addEventListener("keydown", onKeydown);
      ok.onclick = () => cleanup(true);
      cancel.onclick = () => cleanup(false);
    });
  };

  // Small, non-blocking notice for background failures the user should know
  // about but that don't warrant a modal (e.g. a setting that silently
  // failed to save). Auto-dismisses; multiple calls stack briefly rather
  // than clobbering each other.
  window.showToast = function (message, isError = false) {
    let container = document.getElementById("appToastContainer");
    if (!container) {
      container = document.createElement("div");
      container.id = "appToastContainer";
      container.className = "fixed bottom-4 left-4 right-4 z-50 flex flex-col items-center gap-2 pointer-events-none";
      document.body.appendChild(container);
    }
    const toast = document.createElement("div");
    toast.className = `neu-surface px-3 py-2 text-sm max-w-md ${isError ? "text-gb-red" : "text-gb-fg"}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 5000);
  };

  // ---- DOM-dependent one-time setup (runs once the document is parsed) ----
  function initBaseUI() {
    // Inline <span class="material-symbols-rounded">name</span> icons are
    // rendered by the "Material Icons Round" webfont via ligatures; nothing
    // to do here. (Kept as a hook for future icon work.)

    document.querySelectorAll('form[method="post"], form[method="POST"]').forEach((form) => {
      if (!form.querySelector('input[name="csrf_token"]')) {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "csrf_token";
        input.value = window.getCsrfToken();
        form.appendChild(input);
      }
      // Plain (non-JS) form POSTs - login, register, logout, admin login -
      // have no double-submit guard on their own; a fast double-click before
      // the browser starts navigating can fire two requests. This covers
      // all of them in one place rather than repeating it per template.
      form.addEventListener("submit", () => {
        form.querySelectorAll('button[type="submit"], button:not([type])').forEach((btn) => {
          btn.disabled = true;
        });
      });
    });

    const themeOrder = ["light", "dark", "system"];
    const themeIcons = { light: "light_mode", dark: "dark_mode", system: "brightness_auto" };
    const themeMedia = window.matchMedia("(prefers-color-scheme: dark)");

    const getThemeMode = () => document.documentElement.getAttribute("data-theme-mode") || "system";
    const isDarkForMode = (mode) => (mode === "system" ? themeMedia.matches : mode === "dark");

    const applyThemeUi = () => {
      const mode = getThemeMode();
      document.querySelectorAll(".theme-toggle").forEach((toggleBtn) => {
        const icon = toggleBtn.querySelector(".material-symbols-rounded");
        if (!icon) return;
        icon.textContent = themeIcons[mode];
        const titleText =
          mode === "system"
            ? `Theme: System (currently ${isDarkForMode(mode) ? "dark" : "light"}). Click for light.`
            : mode === "dark"
            ? "Theme: Dark. Click for system."
            : "Theme: Light. Click for dark.";
        toggleBtn.setAttribute("data-tooltip", titleText);
        toggleBtn.removeAttribute("title");
        toggleBtn.setAttribute("aria-label", titleText);
      });
    };

    const setThemeMode = (mode) => {
      document.documentElement.setAttribute("data-theme-mode", mode);
      document.documentElement.classList.toggle("dark", isDarkForMode(mode));
      window.TimestatPalette.apply(isDarkForMode(mode));
      try {
        localStorage.setItem("timestat-theme", mode);
      } catch (_err) {}
      applyThemeUi();
    };

    applyThemeUi();
    document.querySelectorAll(".theme-toggle").forEach((toggleBtn) => {
      toggleBtn.addEventListener("click", () => {
        const next = themeOrder[(themeOrder.indexOf(getThemeMode()) + 1) % themeOrder.length];
        setThemeMode(next);
      });
    });

    themeMedia.addEventListener("change", () => {
      if (getThemeMode() !== "system") return;
      document.documentElement.classList.toggle("dark", themeMedia.matches);
      window.TimestatPalette.apply(themeMedia.matches);
      applyThemeUi();
    });

    // Cross-tab sync: another tab changing the theme mode or palette only
    // updates that tab's DOM directly (no cookie/API round trip involved),
    // so without this an already-open second tab keeps showing the old
    // theme until it's reloaded.
    window.addEventListener("storage", (event) => {
      if (event.key === "timestat-theme") {
        const mode = themeOrder.includes(event.newValue) ? event.newValue : "system";
        document.documentElement.setAttribute("data-theme-mode", mode);
        document.documentElement.classList.toggle("dark", isDarkForMode(mode));
        window.TimestatPalette.apply(isDarkForMode(mode));
        applyThemeUi();
      } else if (event.key === "timestat-palette" || event.key === "timestat-custom-color") {
        window.TimestatPalette.apply(isDarkForMode(getThemeMode()));
      }
    });

    const tooltip = document.createElement("div");
    tooltip.id = "customTooltip";
    tooltip.className = "custom-tooltip";
    tooltip.setAttribute("role", "tooltip");
    document.body.appendChild(tooltip);

    let activeTarget = null;
    let activeText = "";
    let lastPointerEvent = null;

    const onPointerMove = (event) => updatePosition(event);

    const showTooltip = (target) => {
      const text = target.getAttribute("data-tooltip");
      if (!text) return;
      activeTarget = target;
      activeText = text;
      tooltip.textContent = text;
      tooltip.classList.add("is-visible");
      // Only track pointer movement while a tooltip is actually visible;
      // avoids running a handler on every mousemove for the page's lifetime.
      document.addEventListener("mousemove", onPointerMove, { passive: true });
    };

    const hideTooltip = () => {
      activeTarget = null;
      tooltip.classList.remove("is-visible");
      document.removeEventListener("mousemove", onPointerMove);
    };

    const updatePosition = (event) => {
      if (!activeTarget) return;
      if (!event || typeof event.clientX !== "number") return;
      lastPointerEvent = event;
      const text = activeTarget.getAttribute("data-tooltip");
      if (text !== activeText) {
        activeText = text;
        tooltip.textContent = text;
      }
      const offset = 12;
      const padding = 10;
      let x = event.clientX + offset;
      let y = event.clientY + offset;
      const rect = tooltip.getBoundingClientRect();
      if (x + rect.width > window.innerWidth - padding) {
        x = Math.max(padding, window.innerWidth - rect.width - padding);
      }
      if (y + rect.height > window.innerHeight - padding) {
        y = Math.max(padding, window.innerHeight - rect.height - padding);
      }
      tooltip.style.transform = `translate(${x}px, ${y}px)`;
    };

    document.addEventListener("mouseover", (event) => {
      if (!(event.target instanceof Element)) return;
      const target = event.target.closest("[data-tooltip]");
      if (!target) return;
      if (target === activeTarget) {
        updatePosition(event);
        return;
      }
      showTooltip(target);
      updatePosition(event);
    });

    document.addEventListener("mouseout", (event) => {
      if (!activeTarget) return;
      if (event.relatedTarget instanceof Element && activeTarget.contains(event.relatedTarget)) return;
      const nextTarget =
        event.relatedTarget instanceof Element
          ? event.relatedTarget.closest("[data-tooltip]")
          : null;
      if (nextTarget) {
        showTooltip(nextTarget);
        if (lastPointerEvent) updatePosition(lastPointerEvent);
        return;
      }
      hideTooltip();
    });

    document.addEventListener(
      "scroll",
      () => {
        if (activeTarget) hideTooltip();
      },
      true
    );

    // Warm the browser/SW cache just before likely navigation. Do not spend
    // scarce bandwidth on clients that explicitly request reduced data.
    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    const prefetchAllowed = !connection || !connection.saveData;
    const prefetched = new Set();
    const prefetchLink = (event) => {
      if (!prefetchAllowed || !(event.target instanceof Element)) return;
      const link = event.target.closest("a[href]");
      if (!link || link.target || link.hasAttribute("download")) return;
      let url;
      try { url = new URL(link.href, window.location.href); } catch (_err) { return; }
      if (url.origin !== window.location.origin || url.pathname.startsWith("/api/")) return;
      if (prefetched.has(url.href)) return;
      prefetched.add(url.href);
      const hint = document.createElement("link");
      hint.rel = "prefetch";
      hint.href = url.href;
      document.head.appendChild(hint);
    };
    document.addEventListener("mouseover", prefetchLink, { passive: true });
    document.addEventListener("touchstart", prefetchLink, { passive: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initBaseUI);
  } else {
    initBaseUI();
  }
})();
