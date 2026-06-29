# TimeStat UI/UX Design Review

**Reviewed files:** `templates/base.html`, `templates/dashboard.html`, `templates/user.html`, `templates/weekly_leaderboard.html`, `templates/all_time_stats.html`, `static/style.css`

---

## Executive Summary

TimeStat has a cohesive neumorphic design language, a well-considered theming system, and thoughtful responsive table layouts. The most significant weaknesses are accessibility gaps — icon-only navigation buttons carry no accessible name and the settings modal lacks dialog semantics and a focus trap, making the app largely unusable with a keyboard or screen reader. Async data fetches have no loading states, so every page briefly renders empty before content appears, which erodes trust on first load. Chart colors are hardcoded to Gruvbox hex values, so they silently break under every other theme palette. Several interaction patterns (browser `alert`/`confirm`, hover-only login code reveal, non-dismissible toasts) work on desktop but fail on touch. Addressing the high-priority items would significantly raise the quality bar without requiring a design overhaul.

---

## Prioritised Recommendations

### High Priority

---

**H1 — Add `aria-label` to every icon-only navigation button**

- **What:** Every nav button in `base.html` (dashboard, leaderboard, all-time stats, settings, theme toggle, logout) is icon-only. The icons have `aria-hidden="true"`, so screen readers see nothing. The `data-tooltip` attribute is mouse-only and not exposed to assistive technology.
- **Why:** A screen reader user navigating by tab encounters a row of unlabelled buttons. The theme toggle partially handles this with `aria-label` (via JS), but the rest do not. This is a WCAG 2.1 Level A failure (1.1.1, 4.1.2).
- **Fix:** Add a static `aria-label` (e.g. `aria-label="Dashboard"`) directly on each `<a>`/`<button>` element. The existing `data-tooltip` text is already the right copy — mirror it to `aria-label`.
- **Effort:** S

---

**H2 — Add dialog semantics and focus trap to the settings modal**

- **What:** The settings modal (`#settingsModal` in `base.html`) has no `role="dialog"`, `aria-modal="true"`, or `aria-labelledby`. Focus is not trapped inside when the modal is open, so tabbing past the last focusable element escapes into the page behind. There is no restoration of focus to the trigger button on close.
- **Why:** A keyboard-only user cannot operate the modal safely. Screen readers will not announce it as a dialog, so users may not know it appeared.
- **Fix:** Add `role="dialog" aria-modal="true" aria-labelledby="settingsModalTitle"` to the `<section>`. Add `id="settingsModalTitle"` to the `<h2>`. Implement a simple focus trap (intercept Tab/Shift+Tab to cycle within focusable children) in the open/close JS. On close, return focus to `#openSettingsBtn`.
- **Effort:** M

---

**H3 — Show loading skeletons or spinners during async data fetches**

- **What:** The dashboard calls four APIs in parallel on load (`loadStatus`, `loadWeeklyLeaderboard`, `loadStats`, `loadRecent`). Until they resolve, tables are empty, charts are blank, and the timer shows `00:00:00`. The same applies to the user profile page and all-time stats. There are no loading indicators of any kind.
- **Why:** Empty-looking sections cause users to assume there is no data, or that the page is broken. The empty-state messages ("No weekly tracked sessions yet") appear momentarily before real data loads, creating false negatives.
- **Fix:** Before firing fetches, add a `loading` class or inject a simple CSS skeleton (a pulsing `bg-gb-bg2` bar) inside each card. Remove on resolve. For the timer panel specifically, showing a spinner in place of `00:00:00` prevents the "0h session" illusion.
- **Effort:** M

---

**H4 — Fix chart colors to use theme CSS variables instead of hardcoded Gruvbox hex**

- **What:** Every `renderPie` call across `dashboard.html`, `user.html`, and `all_time_stats.html` passes the same hardcoded `backgroundColor` array (`"#458588"`, `"#98971a"`, `"#d79921"`, etc.). These are Gruvbox accent colors. Under Nord, Dracula, Catppuccin, One Dark, or any custom palette, the chart slices render in Gruvbox colors while everything else adapts — a jarring inconsistency.
- **Why:** The theming system is a headline feature; having the most visually prominent data elements ignore it undermines that investment.
- **Fix:** Build the color array dynamically from CSS variables at render time, the same way `textColor` already is: e.g. `cssRgb("--gb-red-rgb")`, `cssRgb("--gb-green-rgb")`, `cssRgb("--gb-yellow-rgb")`, etc. This also fixes the chart colors on theme or dark/light mode switch.
- **Effort:** M

---

**H5 — Replace `alert()` / `confirm()` with in-page feedback**

- **What:** `user.html` uses `alert(err.message)` for all async errors and `confirm()` before deletions. The dashboard uses `confirm()` for "Cancel session" and "Delete session". These are native browser dialogs — they are unstyled, block the thread, cannot be themed, and are non-functional in some embedded browser contexts (PWA, certain mobile browsers).
- **Why:** They are inconsistent with the rest of the app's custom design language and break on mobile scenarios. The dashboard already has the correct pattern (`setMessage()` for errors, `confirm()` only for destructive actions) — the user profile page simply never received the same upgrade.
- **Fix:** Add a `setMessage` / inline alert mechanism to `user.html` (mirroring the dashboard). Replace all `alert()` calls with it. For destructive confirmations, add a small inline confirmation row or a mini modal instead of `confirm()`.
- **Effort:** M

---

**H6 — Make the login code reveal keyboard and touch accessible**

- **What:** The login code display (`#settingsLoginCodeBox`) reveals on `mouseenter` and masks on `mouseleave`. Touch users cannot trigger `mouseenter`. Keyboard users cannot either.
- **Why:** The login code is a critical credential. Users on mobile or keyboard-only devices cannot see it at all.
- **Fix:** Replace the hover interaction with a toggle button (e.g. an eye icon button beside the box). The button toggles masked/unmasked state on click. This pattern is universally understood and works on all input methods. Remove the `mouseenter`/`mouseleave` handlers.
- **Effort:** S

---

### Medium Priority

---

**M1 — Add visible text labels to navigation buttons, or at minimum a persistent visible affordance**

- **What:** The five header nav icons (dashboard, leaderboard, all-time, settings, theme) are opaque to first-time users. Tooltips appear only on hover, so mobile users and new desktop users have to guess what each icon does.
- **Why:** Navigation discoverability is fundamental. A row of five identical-sized square buttons with no labels requires users to trial-and-error the interface.
- **Fix:** At a minimum add short text labels below each icon (`text-[10px]`) on screens wider than 640 px. Alternatively, use a single-row chip-style nav with label+icon on `md:` and above, collapsing to icon-only on mobile.
- **Effort:** S

---

**M2 — Auto-clear the `actionMessage` after a short delay**

- **What:** In `dashboard.html`, `setMessage()` writes a success or error string to `#actionMessage` above the timer display. It is never automatically cleared. After starting a session, "Session started." stays on screen indefinitely, even after pausing, finishing, or navigating.
- **Why:** Stale success messages mislead the user about the current state. "Session started." displayed next to an active Pause button has no meaning.
- **Fix:** After a non-error message, schedule a `setTimeout(() => setMessage(""), 4000)`. Clear the timer on the next action. Error messages should persist until the user acts.
- **Effort:** S

---

**M3 — Label the "reduce time" buttons (reduce10Btn / reduce30Btn)**

- **What:** In `dashboard.html`, the two time-reduction buttons (`replay_10`, `replay_30`) have no visible text — only a Material Symbol icon. All other session buttons (Start, Pause, Resume, Finish, Cancel) include text alongside the icon.
- **Why:** The `replay_10` / `replay_30` icons are not universally understood to mean "subtract 10/30 minutes from the session." This inconsistency in button design also breaks the visual rhythm of the session tracker.
- **Fix:** Add short text labels: "−10 min" and "−30 min" alongside the icons, consistent with the other session control buttons. Add appropriate `aria-label` values.
- **Effort:** S

---

**M4 — Remove duplicate navigation buttons from the Weekly Leaderboard dashboard widget**

- **What:** The sidebar Weekly Leaderboard widget on the dashboard ends with two identically styled purple buttons: "Full weekly leaderboard" and "All-time leaderboard". Both destinations are already accessible via the icon nav at the top of every page.
- **Why:** The duplicated buttons add visual noise and imply these actions are more important than the current session. They draw the eye away from the timer.
- **Fix:** Remove both buttons. Replace with a single small text link ("See full leaderboard →") if any affordance is needed, or remove entirely given the nav icons exist.
- **Effort:** S

---

**M5 — Check and enforce sufficient colour contrast for muted text**

- **What:** Several UI elements use low-opacity text: `text-gb-fg/60` (footer, hint subtitles), `text-gb-fg/70` (section subtitles, table headers, leaderboard headers), `text-gb-fg/75` (header subtitle). In Gruvbox light mode, `--gb-fg-rgb` is `60 56 54` (very dark) over `--gb-bg0-rgb` `249 245 215` (very light), so these pass easily. However, under custom palettes with lighter fg tones, the same opacity levels can fall below WCAG AA (4.5:1).
- **Why:** The theming system allows any arbitrary seed color, meaning contrast is not guaranteed for user-chosen palettes. There is also no contrast verification in the palette derivation logic.
- **Fix:** Replace the lowest-opacity values (`/60`) with at least `/75` for body copy. For chart/table headers use `/80`. Consider adding a minimum contrast clamp to `generateCustomVars` in the palette JS.
- **Effort:** M

---

**M6 — Enrich the standalone Weekly Leaderboard page with context and a chart**

- **What:** `weekly_leaderboard.html` is a single card with a table. There are no visualisations, no per-user linking (actually there is a link but no rank badges), no date context beyond a text label, and no "Your rank" highlight.
- **Why:** Users navigate here from the dashboard widget to see more detail. A plain table with the same columns as the widget preview is a letdown. The all-time stats page (which has 3 panels including 2 charts) shows what this page could be.
- **Fix:** Add a bar chart of top-N users' hours. Highlight the current user's row. Add medal icons (gold/silver/bronze) for rank 1–3.
- **Effort:** M

---

**M7 — Hide the history filter "Clear" button when no filters are active**

- **What:** The "Clear" filter button in `dashboard.html` and `user.html` is always visible, even when both the search field and category select are at their defaults.
- **Why:** A "Clear" button for something that has not been set creates unnecessary visual noise and confusion.
- **Fix:** Toggle the button's visibility based on whether `query || category` is non-empty, mirroring how `loadMoreRecentBtn` is toggled.
- **Effort:** S

---

**M8 — Add a back/breadcrumb navigation link to `user.html`**

- **What:** The user profile page header displays the username and subtitle but has no indication of where the user came from (dashboard, leaderboard). The global nav shows all three icons (none highlighted, since `active_page` is not set in the user view).
- **Why:** Without a highlighted active page or a back link, context is lost. Users clicking a username from the leaderboard land on the profile page with no clear "return" path.
- **Fix:** Set `active_page` in the user profile view context, or add a breadcrumb below the header subtitle. A simple "← Back to leaderboard" link using `request.referrer` fallback is sufficient.
- **Effort:** S

---

### Low Priority

---

**L1 — Suppress the neumorphic lift effect on non-interactive `neu-surface` cards**

- **What:** `style.css` gives every `.neu-surface` a `:hover` rule that deepens the shadow and lifts the card (`box-shadow` changes). Static information cards (the pie chart panels, the weekly leaderboard widget, the session history card) get this hover treatment even though they are not clickable.
- **Why:** Applying a hover state to non-interactive elements misleads users into thinking they can click. It also adds visual noise on large screens where the cursor naturally rests over content.
- **Fix:** Remove the `.neu-surface:hover` rule from the base class. Selectively add it only to cards that are genuinely interactive (e.g. `.neu-surface.is-interactive:hover`), or apply it via a utility class.
- **Effort:** S

---

**L2 — Standardise section heading margin spacing**

- **What:** Section `<h2>` elements use inconsistent bottom margin: `mb-3` in some cards (Session Tracker, category charts, leaderboard panels in all_time_stats) and `mb-1` in others (Weekly Leaderboard widget, Personal Breakdown, Team Breakdown on dashboard) before the sub-label paragraph. This results in sections that appear looser or tighter depending on where they appear.
- **Why:** Small spacing inconsistencies create a "hand-assembled" feel that undermines the otherwise coherent design system.
- **Fix:** Standardise to `mb-1` for `h2` when followed by a description paragraph (which then has `mb-2`/`mb-3`), and `mb-3` for `h2` when directly above content. This can be a one-pass find-and-replace in the templates.
- **Effort:** S

---

**L3 — Make the collab notification toast manually dismissible**

- **What:** `showCollabNotification()` creates toasts that auto-remove after 5.5 seconds with no dismiss button and `pointer-events: none` on the container (though individual toasts set `pointer-events-auto`). However, the toast element itself has no dismiss affordance.
- **Why:** If multiple notifications fire in quick succession, they stack without any way to dismiss them early. 5.5 seconds can feel long when there is active work in progress.
- **Fix:** Add a small close button (`×`) inside each toast. Clicking it calls `toast.remove()`. The auto-dismiss timer can remain as a fallback.
- **Effort:** S

---

**L4 — Fix the typo in `dashboard.html` line 77**

- **What:** The weekly leaderboard range label reads `"Top 5 team mebers across the last 7 days"` — "mebers" should be "members".
- **Why:** It is a typo in visible user-facing copy.
- **Fix:** Change `mebers` → `members` in the template string.
- **Effort:** S (trivial)

---

**L5 — Add total hours summary below doughnut charts**

- **What:** The pie/doughnut charts on the dashboard, all-time stats, and user profile pages show proportional breakdowns but display no absolute total. A user cannot tell whether their "Personal Breakdown" represents 2 hours or 200 hours from the chart alone.
- **Why:** Proportional context without absolute scale is only half the picture for a time-tracking app.
- **Fix:** Below each doughnut canvas, add a small `text-gb-fg/80 text-sm` line showing "Total: X.XX hours" calculated from the sum of the dataset values. This is already computed in JS — it just needs to be rendered.
- **Effort:** S

---

**L6 — Fix the `accent-` hardcode on the collab-notify checkbox**

- **What:** In `base.html`, the settings modal checkbox uses `accent-[rgb(104,157,106)]` — a hardcoded Gruvbox aqua value. Under other themes the tick/checkbox fill will not match the palette.
- **Why:** Minor inconsistency, but the same effort that went into the full theming system makes this stand out.
- **Fix:** Replace with `accent-[rgb(var(--gb-aqua-rgb))]` (Tailwind's arbitrary value syntax supports CSS variable references).
- **Effort:** S

---

*End of review. Total items: 6 High, 8 Medium, 6 Low.*
