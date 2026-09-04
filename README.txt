====================================================
  PickMatrix v2.4.2
  Pick Staffing Evaluator - Multi-Site
  Created & managed by ttuyen
====================================================


QUICK START (4 steps):

  Step 1: Install Python
    - https://www.python.org/downloads/  (3.12 or newer)
    - IMPORTANT: check "Add Python to PATH" during install

  Step 2: Log into FCLM (Firefox recommended)
    - Open Firefox, go to https://fclm-portal.amazon.com, log in with Midway

  Step 3: Install the OB Pick Center add-on (for live headcount)
    - Install Tampermonkey: https://www.tampermonkey.net/
    - Install OB Pick Center (v3.7+). If given the script file, add it manually
      in Tampermonkey (Dashboard > + > paste > Save), or install from:
      https://raw.githubusercontent.com/ttuyen099/ob-pick-center/main/ob-pick-center.user.js
    - It auto-detects your site from the Rodeo URL.

  Step 4: Run PickMatrix
    - Double-click "Start Dashboard.bat"  ->  opens http://localhost:8787
    - Keep the terminal window open while using the dashboard


====================================================
KEEP THESE TABS OPEN (same browser, e.g. Firefox)
====================================================

  For headcount and all live data to work, keep these open and logged in:

  1. RODEO - your site's ExSD page  (REQUIRED - this drives everything)
       https://rodeo-iad.amazon.com/<YOUR_FC>/ExSD?yAxis=PROCESS_PATH
       Example: https://rodeo-iad.amazon.com/HOU8/ExSD?yAxis=PROCESS_PATH
       The OB Pick Center panel appears here; it auto-detects your site and
       pushes headcount to PickMatrix every 30 seconds.

  2. PICKING CONSOLE - Pick Workforce for your FC  (source of HC)
       https://picking-console.na.picking.aft.a2z.com
       Log in and keep it open so the workforce feed keeps flowing.

  3. FCLM  (rates, X-Train, logins)
       https://fclm-portal.amazon.com   (logged in with Midway)

  If HC shows "--": the Rodeo tab isn't open/active for your site, or your
  Picking Console session expired. Reopen/refresh both.


====================================================
CHOOSING YOUR SITE
====================================================

  Pick your site any of these ways:
    1. In the dashboard: the "Site" dropdown in the top bar
    2. On launch: Start Dashboard.bat CLT3
    3. A site.txt file next to Start Dashboard.bat containing your FC code

  Defaults to HOU8 if none chosen. Each site keeps its own history and settings.

  Included sites (19):
    HOU8, CLT3, LAS6, MDT4, MCE1, MDT1, PIT2, ORD2, OKC2, SNA4, MKC4, FAT2,
    SAT4, LGB6, LFT1, MEX6, MEX2, BJX1, PHX7

  Add another site: copy sites\CLT3.yaml to sites\<YOURFC>.yaml, set
  warehouse_id (and process_id if different). It appears in the dropdown.


====================================================
WHAT YOU'LL SEE
====================================================

  - Per-path HC:  Total / Active / Inactive  (live from Picking Console, 30s)
  - Rates vs Goal, color-coded
  - Low-Density (OrderPickLowDensityP) split into BOD vs NonCon with rates
  - Path Move Log (+ end-of-shift CSV export)
  - X-Train lookup (live FCLM permissions; logins from the FCLM roster)
  - Attrition tracker


====================================================
ABOUT "NewRodeo master"
====================================================

  You do NOT need NewRodeo master for PickMatrix. Headcount, rates, moves, and
  X-Train all work with just OB Pick Center. NewRodeo master is ONLY needed for
  OB Pick Center's in-Rodeo "Actuals" panel (Plan HC vs live Actuals). Optional.


====================================================
HOW UPDATES WORK:
====================================================

  PickMatrix: checks GitHub on launch; updates only when GitHub is NEWER (and
  self-heals the per-site configs folder if it's ever missing).
  OB Pick Center: updates via Tampermonkey (needs v3.7+).


====================================================
TROUBLESHOOTING:
====================================================

  Terminal closes instantly:
    - Port 8787 already in use by a previous run - close the old instance first
    - Or right-click Start Dashboard.bat > Run as administrator

  "Python not found":
    - Reinstall Python, check "Add Python to PATH", restart your PC

  HC "--" or red FC-mismatch banner:
    - Keep your site's Rodeo ExSD tab open; make sure OB Pick Center v3.7+ is on;
      keep the Picking Console logged in

  My site isn't in the dropdown:
    - Close and relaunch Start Dashboard.bat - it fetches the sites folder

  X-Train empty:
    - Wait ~60-90 seconds after startup for permission verification

  Dashboard stuck loading:
    - Make sure Firefox is open and you've visited fclm-portal.amazon.com


====================================================
FILES IN THIS FOLDER:
====================================================

  Start Dashboard.bat           <- DOUBLE CLICK THIS TO START
  staffing_dashboard_server.py  - Server
  staffing_dashboard.html       - Dashboard UI
  fclm_rate_puller.py           - FCLM data fetcher
  cross_training.py             - FCLM permission checker
  login_lookup.py               - FCLM roster login resolver
  rate_history.py               - Per-site historical data + backfill
  learning_engine.py            - Move recommendations + login bridge
  updater.py                    - Auto-update from GitHub
  config.yaml                   - FCLM settings (Slack blank by default)
  staffing_config.yaml          - Default goals and paths
  sites\                        - Per-site configs (19 FCs)
  requirements.txt              - Python packages
  version.txt                   - Current version
  CHANGELOG.md                  - Version history
  README.txt                    - This file

  NOTE: No associate CSV is bundled or needed - cross-training is live from FCLM.


====================================================
  PickMatrix v2.4.2 | ttuyen | Multi-Site Pick Operations
====================================================
