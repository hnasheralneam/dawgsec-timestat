/** @type {import('tailwindcss').Config} */
// Mirrors the theme that used to be defined inline in templates/base.html for
// the Tailwind Play CDN. The compiled output is committed as static/tailwind.css.
module.exports = {
  darkMode: "class",
  content: ["./templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        gb: {
          bg0: "rgb(var(--gb-bg0-rgb) / <alpha-value>)",
          bg1: "rgb(var(--gb-bg1-rgb) / <alpha-value>)",
          bg2: "rgb(var(--gb-bg2-rgb) / <alpha-value>)",
          fg: "rgb(var(--gb-fg-rgb) / <alpha-value>)",
          red: "rgb(var(--gb-red-rgb) / <alpha-value>)",
          green: "rgb(var(--gb-green-rgb) / <alpha-value>)",
          yellow: "rgb(var(--gb-yellow-rgb) / <alpha-value>)",
          aqua: "rgb(var(--gb-aqua-rgb) / <alpha-value>)",
          blue: "rgb(var(--gb-blue-rgb) / <alpha-value>)",
          purple: "rgb(var(--gb-purple-rgb) / <alpha-value>)",
        },
      },
    },
  },
};