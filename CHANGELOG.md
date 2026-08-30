# PickMatrix — Changelog

All notable changes to PickMatrix (HOU8 Pick Staffing Evaluator) are documented here.
Versions map to `version.txt`; users auto-update on launch when the GitHub version is newer.

## [1.9.2] — 2026-08-30

### Added — Path Move Log (Overview tab)
- **New Path Move Log panel** on the Overview tab showing every picker that changed
  process paths: **Login | From → To | Time**, newest first.
- **Two-column Overview layout** — move log on the left, process-path rate cards on
  the right, sized to match (no more tiny/cramped column). Collapses to a single
  column on narrow screens and when a specific path/tab is selected.
- **Scrollable log with a sticky header**; the scroll position is preserved across
  the 30-second auto-refresh (it no longer jumps back to the top).
- **Time-range filter** ("This shift" / Last 4h / Last 12h / All) so users can scope
  the log to the shift they are working. "This shift" tracks the dashboard's shift
  window automatically.
- **End-of-shift CSV export** (`Export CSV` button). One row per picker with:
  Total Moves, Moves To BOD, Moves To NonCon, Moves To Low-Density (BOD+NonCon),
  Distinct Paths Worked, per-path durations (minutes spent in each path before the
  next move), Longest Path, and First/Last move timestamps.

### Changed — Move detection now uses the Picking Console
- Path-move detection is driven **entirely by the Picking Console workforce feed**
  (login + exact `PP*` process path), refreshed on each ~30s push — not FCLM.
  FCLM-based move detection is disabled and legacy FCLM-named rows are hidden from
  the log so it reflects real console process paths.
- **Login resolution bridged from the Picking Console.** Logins that the Certificate
  CSV could not resolve are now recovered by joining the console feed to FCLM on the
  associate name (used internally only; the name is never displayed). Resolved
  logins are persisted so they survive with the console closed.
- Process-path labels shown as their Picking Console names (e.g. `PPSingleOP`,
  `PPSingleOPVNA`, `PPMultiBldgWide`).
- **Low-density** is defined strictly as `PPSingleOPBOD` and `PPSingleOPNonCon`.

### Fixed
- **History tooltip cutoff.** Hovering a login in a low-headcount path no longer
  pushes the rate-history tooltip off the bottom of the screen. The tooltip is now
  rendered at the document body (outside the cards' `backdrop-filter`, which was
  re-anchoring it) and measured/clamped to stay fully in the viewport.
- Loading splash now reads "PickMatrix loading".

## [1.9.1] — Fix Firefox cookie read failures during concurrent access
## [1.9.0] — Viewport-aware tooltip positioning with scroll support
## [1.8.9] — Tooltip max-height with scroll, simplified positioning
## [1.8.8] — Tooltip shows above row when in bottom half of screen
## [1.8.7] — Serve .txt files, fix footer version 404
## [1.8.6] — Dynamic footer version display from version.txt
## [1.8.5] — Measure actual tooltip height before positioning
## [1.8.4] — Dynamic tooltip follows hovered row
## [1.8.3] — Fixed-viewport tooltip positioning on scroll
## [1.8.2] — GitHub API version check (CDN cache-proof); manual restart after update
## [1.8.1] — Tooltip, HC mapping, and X-Train fixes
## [1.8.0] — X-Train/History status indicators, 30s status auto-refresh,
            HC counts only mapped pick paths, backfill fix, README rewrite
