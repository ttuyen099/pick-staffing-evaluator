# PickMatrix — Changelog

All notable changes to PickMatrix (Pick Staffing Evaluator) are documented here.
Versions map to `version.txt`; users auto-update on launch when the GitHub version is newer.

## [2.5.0] — 9 more sites

### Added
- **DEN8, CMH2, GSO1, GDL1, MTY1, MTY3, SMF6, LIT2, MDW6** (28 sites total;
  MDT1 was already present). Health-checked: FCLM paths load and the employee
  roster resolves logins on all 10 (MTY3 at 68/72 — the remaining 4 are very
  recent hires not yet in the roster snapshot; resolves automatically as the
  roster refreshes).

## [2.4.3] — Updated README (required tabs) now ships to users

### Changed
- README rewritten for the current multi-site build and now added to the
  auto-update file list (it was previously never pushed, so users had the old
  v1.8 README). Adds a clear "Keep these tabs open" section: your site's Rodeo
  ExSD page (drives everything), Picking Console (HC source), and FCLM.

## [2.4.2] — Reliable per-site config delivery

### Fixed
- **The sites/ folder now reliably reaches users on update.** Previously, a user
  updating from an older `updater.py` ran the old file list (which predated
  per-site configs), so the sites folder arrived a cycle late or not at all.
  `Start Dashboard.bat` now runs the updater twice (so a freshly-downloaded
  updater applies its own newer file list in the same session) and force-updates
  if the sites folder is missing — so everyone gets their site in the dropdown.

## [2.4.1] — Cleaner tab title

### Changed
- Browser tab title is now just "PickMatrix" (no site code). The active site
  is still shown in the header badge and Site dropdown.

## [2.4.0] — 4 more sites

### Added
- **MEX6, MEX2, BJX1, PHX7** (19 sites total). Health-checked: FCLM paths load
  and the employee roster resolves logins (100% associate→login match on all).

## [2.3.0] — 2 more sites

### Added
- **LGB6** and **LFT1** (`sites/LGB6.yaml`, `sites/LFT1.yaml`). 15 sites total.
  Health-checked: FCLM paths load and the employee roster resolves logins
  (100% associate→login match on both).

## [2.2.1] — Login display fix

### Fixed
- **Associate tables and X-Train show real logins instead of employee IDs.**
  The associate row now falls back to the live `eid_to_login` map (FCLM roster
  + Picking Console bridge) when the associate's own login field hasn't been
  populated yet, so logins appear as soon as the roster resolves — no waiting
  for the next full FCLM fetch.

## [2.2.0] — More sites

### Added
- **11 new sites**: LAS6, MDT4, MCE1, MDT1, PIT2, ORD2, OKC2, SNA4, MKC4,
  FAT2, SAT4 (each `sites/<FC>.yaml`). All appear in the in-dashboard Site
  dropdown. Path names auto-discover from each site's FCLM report; logins
  resolve from that site's employee roster.
- Health-checked all sites: FCLM pick paths load and the full employee roster
  resolves logins (100% associate→login match on spot checks).

## [2.1.5] — Reliable login resolution via FCLM roster

### Added
- **Login lookup from the FCLM Employee Roster** (`login_lookup.py`): one call
  returns the whole FC's employee_id→login map. Associates and X-Train now show
  real logins instead of employee IDs, with no CSV and no dependence on
  name-matching the Picking Console feed. Resolved logins are persisted per site.

## [2.1.4] — Permission verification stability

### Fixed
- **X-Train no longer stuck "verifying".** Permission checks are now
  single-flight (one at a time) and remember every identifier attempted (even
  those with no permissions), so high-frequency console pushes can't stack
  overlapping verification threads or re-check the same people forever.
- Workforce POST responses are disconnect-safe (no more `WinError 10053`
  tracebacks when the add-on's short-timeout client drops the connection).

## [2.1.3] — Cross-training is FCLM-only; wrong-site data guard

### Changed
- **Cross-training now comes solely from live FCLM permissions.** The
  Certificate-tracking CSV is no longer loaded or bundled. X-Train is always
  correct for the active site with zero setup.
- **X-Train now also verifies pickers in the Picking Console feed** (by login),
  not just associates with FCLM rate rows — so on-shift pickers appear even
  before they log pick time. Verification is incremental (no repeat checks).

### Added
- **In-dashboard Site selector** (top-bar dropdown) that switches sites and
  restarts cleanly onto the chosen site's config + per-site history DB.
- **FC-mismatch guard:** the server rejects OB Pick Center workforce pushes
  whose FC doesn't match the active site, and the dashboard shows a warning.
  Requires OB Pick Center v3.6+ (which tags its FC on each push).

### Fixed
- **Login resolution without a CSV:** logins resolve via the per-site Picking
  Console bridge, so the X-Train "Login" column no longer shows raw employee IDs.
- **Auto-updater no longer overwrites a newer local build** — it updates only
  when the GitHub version is strictly newer.
- **Start Dashboard.bat** site-selection block rewritten (cmd-safe) so the
  terminal no longer closes on launch.

## [2.1.0] — Multi-site support

**PickMatrix now works for more than one FC.**

### Added
- **Site selection.** Pick your site via `Start Dashboard.bat CLT3`, a `site.txt`
  file, or the `PICKMATRIX_SITE` env var. Per-site settings live in
  `sites/<FC>.yaml` (warehouse_id, process_id, path goals, port).
- **CLT3 support** (`sites/CLT3.yaml`). Path names and FCLM function IDs
  auto-discover from CLT3's FCLM report, so no manual ID entry is needed.
- **Active site badge** in the dashboard header, and the page title reflects
  the site.
- **Per-site data isolation.** Rate history + move log are stored in a
  per-site database (`rate_history_<FC>.db`), so sites never mix data.
- **Per-site local settings.** Custom rate goals and attrition entries are now
  keyed by site in your browser (no bleed between FCs).

### Notes
- HOU8 behavior is unchanged when no site is selected (uses the existing
  config and shared history DB).
- To add another site, copy `sites/CLT3.yaml` to `sites/<FC>.yaml` and set its
  `warehouse_id` (and `process_id` if different).

## [2.0.0] — Picking Console is now the source of truth for Headcount

**Headline: HC now comes straight from the Picking Console workforce feed.**
No more guessing headcount from FCLM paid hours — every path shows a real
Total / Active / Inactive breakdown driven by the live console roster.

### Added
- **Total / Active / Inactive HC per path.** Each path card now shows three
  numbers instead of one:
  - **Total** — every picker the Picking Console lists on that path.
  - **Active** — pickers whose console status is Active.
  - **Inactive** — on the path but not currently active.
  The top-bar "Pick HC" chip shows the same three numbers site-wide
  (logins de-duplicated across paths).
- **Low-Density BOD vs NonCon breakdown.** FCLM collapses `PPSingleOPBOD` and
  `PPSingleOPNonCon` into one `OrderPickLowDensityP` bucket. The
  OrderPickLowDensityP card now shows **BOD** and **NonCon** as the primary HC
  view — the Picking Console identifies who's on each sub-path, and FCLM
  supplies each sub-path's average rate (UPH).

### Changed
- Headcount no longer falls back to FCLM `paid_hours`. If the Picking Console
  feed hasn't arrived yet, HC shows `--` instead of an estimate.
- Live HC polling (every 30s) refreshes the full Total/Active/Inactive
  breakdown and the Low-Density BOD/NonCon split.
- Path tabs and the summary chip use the console counts.

### Fixed
- Path-card metric row no longer overflows past the card edge with the wider
  HC display; the Low-Density card stays the same height as the others.

### Requirements
- **Requires OB Pick Center Tampermonkey v3.5+** (v3.6+ recommended), which
  pushes the full workforce roster (every picker + active/inactive status) to
  PickMatrix. On older script versions, HC stays blank (`--`). Keep Rodeo /
  Picking Console open in the browser as before.

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
