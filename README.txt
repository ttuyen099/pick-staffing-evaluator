====================================================
  PickMatrix v1.8
  Pick Staffing Evaluator - HOU8
  Created & managed by ttuyen
====================================================


QUICK START (3 steps):

  Step 1: Install Python
    - Go to https://www.python.org/downloads/
    - Download Python 3.12 or newer
    - IMPORTANT: Check the box "Add Python to PATH" during install
    - Click Install

  Step 2: Log into FCLM
    - Open Firefox
    - Go to https://fclm-portal.amazon.com
    - Log in with Midway (just need to visit the page once)

  Step 3: Run PickMatrix
    - Open the PickMatrix folder
    - Double-click "Start Dashboard.bat"
    - Dashboard opens automatically at http://localhost:8787
    - Keep the terminal window open while using the dashboard


====================================================
THAT'S IT. YOU'RE DONE.
====================================================


FOR LIVE HEADCOUNT TRACKING (optional):
  - Open Rodeo in your browser
  - Make sure OB Pick Center Tampermonkey v3.5+ is installed
  - Keep Rodeo open while using PickMatrix
  - HC updates every 30 seconds automatically


====================================================
WHAT YOU'LL SEE ON THE DASHBOARD:
====================================================

TOP BAR:
  - Shift selector (Night/Day)
  - Start/End time for data window
  - Auto-refresh interval (15 or 30 min)
  - Refresh Now button

SUMMARY:
  - Paths count
  - Hitting/Missing rate count
  - Pick Total UPH
  - Pick HC (live headcount)
  - Attrition count
  - X-Train availability per department
  - Last Updated timestamp
  - X-Train and History loading status

PATH TABS:
  - Click any path to see individual associates
  - Shows HC per path
  - Overview shows all path cards

ASSOCIATE TABLE (click a path):
  - CURRENTLY PICKING section (green header)
  - PRIOR / INACTIVE section (grey, dimmed)
  - Name, Login, Rate, Units, Hours, X-Train dots
  - Hover name to see Strengths/Opportunities
  - Move? button for staffing recommendations

X-TRAIN TAB:
  - Search any login
  - Shows verified FCLM permissions per associate
  - Color dots: Pick/Stow/Receive/Pack/Ship Dock/ICQA/VRets

ATTRITION TAB:
  - Type login + departure time
  - Tracks who's leaving early
  - Shows in summary header


====================================================
HOW UPDATES WORK:
====================================================

  - Dashboard checks for updates automatically
  - If available: orange "Update Now" button appears in footer
  - Click it and wait 5 seconds - done
  - Or just restart "Start Dashboard.bat" - it updates on launch


====================================================
TROUBLESHOOTING:
====================================================

  "Python not found":
    - Reinstall Python from python.org
    - Make sure "Add Python to PATH" is checked
    - Restart your computer after install

  Dashboard stuck on loading:
    - Make sure Firefox is open
    - Visit https://fclm-portal.amazon.com in Firefox
    - Restart Start Dashboard.bat

  X-Train shows "loading" or "verifying":
    - This takes ~90 seconds on startup
    - Wait and it will auto-populate
    - Status shows in footer and summary area

  HC shows "--" or 0:
    - Open Rodeo with OB Pick Center v3.5+
    - HC updates every 30 seconds from Picking Console

  No "Currently Picking" section:
    - Same as above - needs Rodeo open
    - Without Rodeo, all associates show in one list

  Terminal closes immediately:
    - Right-click Start Dashboard.bat > Run as administrator
    - Or open Command Prompt, cd to PickMatrix folder, type:
      python staffing_dashboard_server.py

  Data shows old/stale rates:
    - Click "Refresh Now"
    - Or wait for auto-refresh (15 min default)
    - Change interval with the Auto-Refresh dropdown


====================================================
HISTORICAL DATA:
====================================================

  - Tool automatically collects rate data each shift
  - On first start, backfills last 14 days from FCLM
  - Stored in: AppData\Local\PickMatrix\rate_history.db
  - Survives updates and folder moves
  - Used for move recommendations and hover tooltips
  - 30-day retention, recommendations use last 14 days


====================================================
RATE GOALS (editable):
====================================================

  Default goals (click the number on dashboard to change):
    RF Pick Singles:       50 UPH
    OrderPickVNA:          35 UPH
    Orderpicker Pick:      30 UPH
    OrderPickLowDensityP:  20 UPH
    OrderPick SIOC:        20 UPH
    MultiRelayPick:        20 UPH
    RF Pick:                5 UPH
    Giftwrap Picking:       5 UPH

  Goals save per browser (localStorage) - each user sets their own.
  To change defaults for everyone: edit staffing_config.yaml


====================================================
FILES IN THIS FOLDER:
====================================================

  Start Dashboard.bat      <- DOUBLE CLICK THIS TO START
  staffing_dashboard_server.py  - Server
  staffing_dashboard.html       - Dashboard UI
  fclm_rate_puller.py           - FCLM data fetcher
  cross_training.py             - Permissions checker
  rate_history.py               - Historical data + backfill
  learning_engine.py            - Move recommendations AI
  updater.py                    - Auto-update from GitHub
  config.yaml                   - FCLM settings (warehouse)
  staffing_config.yaml          - Goals and paths
  Certificate-tracking.csv      - Training data for logins
  requirements.txt              - Python packages
  version.txt                   - Current version
  README.txt                    - This file


====================================================
  PickMatrix v1.8 | ttuyen | HOU8 Pick Operations
====================================================
