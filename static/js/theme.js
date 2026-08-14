/*
 * TimeStat theme engine.
 *
 * Extracted from the inline <head> block of templates/base.html so it can be
 * cached by the service worker instead of being re-parsed on every page load.
 * It is loaded synchronously (non-deferred) so non-default palettes are applied
 * before first paint and there is no flash of the wrong theme.
 *
 * Depends on `window.__SERVER_THEME__` (set by a tiny inline script in
 * base.html before this file is loaded).
 */
window.TimestatPalette = (function () {
  const STORAGE_PALETTE_KEY = "timestat-palette";
  const STORAGE_COLOR_KEY = "timestat-custom-color";
  const DEFAULT_CUSTOM_COLOR = "#458588";

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  function hexToRgb(hex) {
    const clean = hex.replace("#", "");
    return [
      parseInt(clean.substring(0, 2), 16),
      parseInt(clean.substring(2, 4), 16),
      parseInt(clean.substring(4, 6), 16),
    ];
  }

  function rgbToHsl([r, g, b]) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    const l = (max + min) / 2;
    const d = max - min;
    let h = 0, s = 0;
    if (d !== 0) {
      s = d / (1 - Math.abs(2 * l - 1));
      switch (max) {
        case r: h = 60 * (((g - b) / d) % 6); break;
        case g: h = 60 * ((b - r) / d + 2); break;
        default: h = 60 * ((r - g) / d + 4);
      }
    }
    if (h < 0) h += 360;
    return [h, s, l];
  }

  function hslToRgb([h, s, l]) {
    const c = (1 - Math.abs(2 * l - 1)) * s;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = l - c / 2;
    let rgb;
    if (h < 60) rgb = [c, x, 0];
    else if (h < 120) rgb = [x, c, 0];
    else if (h < 180) rgb = [0, c, x];
    else if (h < 240) rgb = [0, x, c];
    else if (h < 300) rgb = [x, 0, c];
    else rgb = [c, 0, x];
    return rgb.map((v) => Math.round(clamp(v + m, 0, 1) * 255));
  }

  const triplet = ([r, g, b]) => `${r} ${g} ${b}`;

  function adjustLightness(rgb, deltaL) {
    const [h, s, l] = rgbToHsl(rgb);
    return hslToRgb([h, s, clamp(l + deltaL, 0, 1)]);
  }

  function deriveShadowHighlight(bg0rgb, isDark) {
    return isDark
      ? { shadow: adjustLightness(bg0rgb, -0.11), highlight: adjustLightness(bg0rgb, 0.075) }
      : { shadow: adjustLightness(bg0rgb, -0.18), highlight: adjustLightness(bg0rgb, 0.03) };
  }

  const PRESETS = {
    nord: {
      light: { bg0: [236, 239, 244], bg1: [229, 233, 240], bg2: [216, 222, 233], fg: [46, 52, 64], red: [191, 97, 106], green: [163, 190, 140], yellow: [235, 203, 139], aqua: [143, 188, 187], blue: [129, 161, 193], purple: [180, 142, 173] },
      dark: { bg0: [46, 52, 64], bg1: [59, 66, 82], bg2: [67, 76, 94], fg: [236, 239, 244], red: [191, 97, 106], green: [163, 190, 140], yellow: [235, 203, 139], aqua: [143, 188, 187], blue: [129, 161, 193], purple: [180, 142, 173] },
    },
    dracula: {
      light: { bg0: [248, 248, 242], bg1: [233, 233, 228], bg2: [214, 214, 209], fg: [33, 34, 44], red: [204, 40, 40], green: [40, 160, 75], yellow: [180, 140, 10], aqua: [20, 150, 170], blue: [110, 90, 200], purple: [200, 60, 150] },
      dark: { bg0: [40, 42, 54], bg1: [68, 71, 90], bg2: [88, 91, 112], fg: [248, 248, 242], red: [255, 85, 85], green: [80, 250, 123], yellow: [241, 250, 140], aqua: [139, 233, 253], blue: [189, 147, 249], purple: [255, 121, 198] },
    },
    solarized: {
      light: { bg0: [253, 246, 227], bg1: [238, 232, 213], bg2: [147, 161, 161], fg: [7, 54, 66], red: [220, 50, 47], green: [133, 153, 0], yellow: [181, 137, 0], aqua: [42, 161, 152], blue: [38, 139, 210], purple: [108, 113, 196] },
      dark: { bg0: [0, 43, 54], bg1: [7, 54, 66], bg2: [88, 110, 117], fg: [131, 148, 150], red: [220, 50, 47], green: [133, 153, 0], yellow: [181, 137, 0], aqua: [42, 161, 152], blue: [38, 139, 210], purple: [108, 113, 196] },
    },
    catppuccin: {
      light: { bg0: [239, 241, 245], bg1: [230, 233, 239], bg2: [204, 208, 218], fg: [76, 79, 105], red: [210, 15, 57], green: [64, 160, 43], yellow: [223, 142, 29], aqua: [23, 146, 153], blue: [30, 102, 245], purple: [136, 57, 239] },
      dark: { bg0: [30, 30, 46], bg1: [24, 24, 37], bg2: [49, 50, 68], fg: [205, 214, 244], red: [243, 139, 168], green: [166, 227, 161], yellow: [249, 226, 175], aqua: [148, 226, 213], blue: [137, 180, 250], purple: [203, 166, 247] },
    },
    onedark: {
      light: { bg0: [250, 250, 250], bg1: [240, 240, 240], bg2: [212, 212, 212], fg: [56, 58, 66], red: [228, 86, 73], green: [80, 161, 79], yellow: [193, 132, 1], aqua: [1, 132, 188], blue: [64, 120, 242], purple: [166, 38, 164] },
      dark: { bg0: [40, 44, 52], bg1: [33, 37, 43], bg2: [62, 68, 81], fg: [171, 178, 191], red: [224, 108, 117], green: [152, 195, 121], yellow: [229, 192, 123], aqua: [86, 182, 194], blue: [97, 175, 239], purple: [198, 120, 221] },
    },
  };

  const ACCENT_HUES = { red: 8, yellow: 48, green: 110, aqua: 175, blue: 215, purple: 280 };

  function generateCustomVars(hex, isDark) {
    const seedRgb = hexToRgb(hex);
    const [seedHue, seedSat] = rgbToHsl(seedRgb);
    const delta = seedHue - ACCENT_HUES.blue;
    const wrap = (h) => ((h % 360) + 360) % 360;
    const accentSat = clamp(seedSat * 0.6 + 0.4, 0.45, 0.78);
    const accentLightness = isDark ? 0.68 : 0.42;
    const mkAccent = (key, lightness) => triplet(hslToRgb([wrap(ACCENT_HUES[key] + delta), accentSat, lightness]));
    const bgSat = clamp(seedSat * 0.5, 0.12, 0.32);
    const bg0hsl = isDark ? [seedHue, bgSat, 0.15] : [seedHue, bgSat, 0.94];
    const bg1hsl = isDark ? [seedHue, bgSat, 0.21] : [seedHue, bgSat, 0.88];
    const bg2hsl = isDark ? [seedHue, bgSat, 0.28] : [seedHue, bgSat, 0.8];
    const fghsl = isDark ? [seedHue, bgSat * 0.6, 0.9] : [seedHue, bgSat * 0.6, 0.2];
    const bg0 = hslToRgb(bg0hsl);
    const { shadow, highlight } = deriveShadowHighlight(bg0, isDark);
    return {
      "--gb-bg0-rgb": triplet(bg0),
      "--gb-bg1-rgb": triplet(hslToRgb(bg1hsl)),
      "--gb-bg2-rgb": triplet(hslToRgb(bg2hsl)),
      "--gb-fg-rgb": triplet(hslToRgb(fghsl)),
      "--gb-red-rgb": mkAccent("red", accentLightness),
      "--gb-green-rgb": mkAccent("green", accentLightness),
      "--gb-yellow-rgb": mkAccent("yellow", isDark ? 0.72 : 0.46),
      "--gb-aqua-rgb": mkAccent("aqua", accentLightness),
      "--gb-blue-rgb": mkAccent("blue", accentLightness),
      "--gb-purple-rgb": mkAccent("purple", accentLightness),
      "--gb-shadow-rgb": triplet(shadow),
      "--gb-highlight-rgb": triplet(highlight),
    };
  }

  function presetVars(name, isDark) {
    const preset = PRESETS[name];
    if (!preset) return null;
    const mode = isDark ? preset.dark : preset.light;
    const { shadow, highlight } = deriveShadowHighlight(mode.bg0, isDark);
    return {
      "--gb-bg0-rgb": triplet(mode.bg0),
      "--gb-bg1-rgb": triplet(mode.bg1),
      "--gb-bg2-rgb": triplet(mode.bg2),
      "--gb-fg-rgb": triplet(mode.fg),
      "--gb-red-rgb": triplet(mode.red),
      "--gb-green-rgb": triplet(mode.green),
      "--gb-yellow-rgb": triplet(mode.yellow),
      "--gb-aqua-rgb": triplet(mode.aqua),
      "--gb-blue-rgb": triplet(mode.blue),
      "--gb-purple-rgb": triplet(mode.purple),
      "--gb-shadow-rgb": triplet(shadow),
      "--gb-highlight-rgb": triplet(highlight),
    };
  }

  const VAR_NAMES = ["--gb-bg0-rgb", "--gb-bg1-rgb", "--gb-bg2-rgb", "--gb-fg-rgb", "--gb-red-rgb", "--gb-green-rgb", "--gb-yellow-rgb", "--gb-aqua-rgb", "--gb-blue-rgb", "--gb-purple-rgb", "--gb-shadow-rgb", "--gb-highlight-rgb"];

  function getStoredPalette() {
    if (window.__SERVER_THEME__) {
      const { palette, color } = window.__SERVER_THEME__;
      try {
        localStorage.setItem(STORAGE_PALETTE_KEY, palette);
        localStorage.setItem(STORAGE_COLOR_KEY, color);
      } catch (_err) {}
      return { palette, color };
    }
    let palette = "gruvbox";
    let color = DEFAULT_CUSTOM_COLOR;
    try {
      const storedPalette = localStorage.getItem(STORAGE_PALETTE_KEY);
      if (storedPalette) palette = storedPalette;
      const storedColor = localStorage.getItem(STORAGE_COLOR_KEY);
      if (storedColor) color = storedColor;
    } catch (_err) {}
    return { palette, color };
  }

  function syncToServer(palette, color) {
    if (!window.__SERVER_THEME__) return;
    const csrfToken = window.getCsrfToken ? window.getCsrfToken() : "";
    fetch("/api/user/theme", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ palette, custom_color: color }),
    }).catch(() => {});
  }

  function apply(isDark) {
    const { palette, color } = getStoredPalette();
    const root = document.documentElement.style;
    if (palette === "gruvbox") {
      VAR_NAMES.forEach((name) => root.removeProperty(name));
      return;
    }
    const vars = palette === "custom" ? generateCustomVars(color, isDark) : presetVars(palette, isDark);
    if (!vars) {
      VAR_NAMES.forEach((name) => root.removeProperty(name));
      return;
    }
    Object.entries(vars).forEach(([name, value]) => root.setProperty(name, value));
  }

  function setPalette(name) {
    try {
      localStorage.setItem(STORAGE_PALETTE_KEY, name);
    } catch (_err) {}
    if (window.__SERVER_THEME__) window.__SERVER_THEME__.palette = name;
    apply(document.documentElement.classList.contains("dark"));
    syncToServer(name, getStoredPalette().color);
  }

  function setCustomColor(hex) {
    try {
      localStorage.setItem(STORAGE_COLOR_KEY, hex);
    } catch (_err) {}
    if (window.__SERVER_THEME__) window.__SERVER_THEME__.color = hex;
  }

  return { apply, setPalette, setCustomColor, getStoredPalette, presetNames: Object.keys(PRESETS), DEFAULT_CUSTOM_COLOR };
})();

(function () {
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  let savedTheme = null;
  try {
    savedTheme = localStorage.getItem("timestat-theme");
  } catch (_err) {}
  const mode = ["light", "dark", "system"].includes(savedTheme) ? savedTheme : "system";
  const useDark = mode === "system" ? prefersDark : mode === "dark";
  if (useDark) document.documentElement.classList.add("dark");
  document.documentElement.setAttribute("data-theme-mode", mode);
  window.TimestatPalette.apply(useDark);
})();