============================================================
  STAFFING DASHBOARD - Pick Operations
  HOU8 | Live Rate Monitor & Staffing Evaluator
============================================================

WHAT THIS TOOL DOES:
- Shows live UPH rates for all Pick process paths
- Shows individual associates per path with their rates
- Identifies who is CURRENTLY PICKING vs who was prior/inactive
- Shows verified cross-training permissions (which paths each AA can work)
- Recommends staffing moves based on 14-day historical performance
- Auto-refreshes every 15 or 30 minutes
- Learns from move decisions to improve recommendations over time

============================================================
FIRST TIME SETUP:
============================================================

1. Make sure Python 3.10+ is installed
   (Download from https://www.python.org/downloads/ if needed)

2. Run setup.bat (double-click it)
   - This installs required Python packages
   - Creates a desktop shortcut

3. Make sure Firefox is open and you're authenticated to:
   https://fclm-portal.amazon.com
   (Just visit the page once in Firefox - the tool auto-extracts your cookies)

4. (Optional) For real-time active picker tracking:
   - Install the OB Pick Center Tampermonkey script (v3.5+)
   - Keep your Rodeo page open while using the dashboard

============================================================
HOW TO RUN:
============================================================

Option A: Double-click "Staffing Dashboard" shortcut on desktop
Option B: Double-click run_staffing_dashboard.bat in this folder
Option C: Open terminal and run: python staffing_dashboard_server.py

The dashboard opens automatically in your browser at:
http://localhost:8787

============================================================
USING THE DASHBOARD:
============================================================

TOP BAR:
- Shift selector (Night/Day) - auto-detects based on time
- Start/End time - defaults to current shift window
- Apply - fetches data for custom time range
- Reset - snaps back to shift start -> now
- Auto-Refresh - 5/15/30 min or Off
- Refresh Now - manual immediate refresh

PATH TABS:
- "All Paths" - shows grid overview of all paths
- Click any path name - shows full associate table
- "X-Train Lookup" - search any login to see their permissions

PATH CARDS:
- Current UPH (color-coded: green=hitting, red=missing)
- Goal UPH (editable - click to change)
- Units and Hours
- Progress bar with target marker
- Rate vs Goal and % of Goal

ASSOCIATE TABLE (click a path tab):
- CURRENTLY PICKING section - green header, active pickers
- PRIOR / INACTIVE section - grey header, dimmed, earlier pickers
- Hover name/login to see recommended/not recommended paths
- Rate pill color: green=above goal, yellow=at goal, red=below
- X-Train dots show verified FCLM permissions
- Move? button shows days of historical data, click for recommendation

MOVE RECOMMENDATIONS:
- Click the "Move?" button on any associate
- Shows their 14-day performance history per path
- Select a destination path and goal
- Click "Analyze" for a verdict (GOOD MOVE / OKAY MOVE / RISKY MOVE / NO DATA)
- Click "Confirm Move" to log the decision (system tracks outcome)

============================================================
RATE EXPECTATIONS (GOALS):
============================================================

Default goals (editable in dashboard or config):
  RF Pick Singles:       50 UPH
  OrderPickVNA:          35 UPH
  Orderpicker Pick:      30 UPH
  OrderPickLowDensityP:  20 UPH
  OrderPick SIOC:        20 UPH
  MultiRelayPick:        20 UPH
  RF Pick:                5 UPH
  Giftwrap Picking:       5 UPH

To change defaults permanently, edit staffing_config.yaml

============================================================
FILES IN THIS FOLDER:
============================================================

MAIN FILES:
  staffing_dashboard_server.py  - The server (run this)
  staffing_dashboard.html       - Dashboard UI
  staffing_config.yaml          - Configuration (goals, paths, settings)
  config.yaml                   - FCLM auth settings (warehouse, cookies)

MODULES:
  cross_training.py             - Parses training CSV + FCLM permissions
  rate_history.py               - Historical rate database (SQLite)
  learning_engine.py            - Move tracking + recommendation learning
  staffing_evaluator.py         - CLI-based staffing evaluator (original)

LAUNCHERS:
  run_staffing_dashboard.bat    - Launch the dashboard server
  run_staffing_evaluator.bat    - Launch the CLI evaluator
  setup.bat                     - First-time setup

DATA FILES (auto-generated):
  rate_history.db               - SQLite database (historical rates + moves)
  last_response_debug.html      - Last FCLM response (for debugging)

============================================================
CROSS-TRAINING DATA:
============================================================

The tool reads Umbrella training data from:
  C:\Users\<you>\Downloads\Certificate-tracking*.csv

To update: Re-export from QuickSight and save to Downloads.
The tool auto-finds the latest file on each startup.

Verified against FCLM permissions (ground truth):
- Only shows X-Train dots for associates with ACTUAL active permissions
- Checks: Pick, Pack, Stow, Receive, Ship Dock, ICQA
- Background check runs ~90 seconds after each data refresh

============================================================
TROUBLESHOOTING:
============================================================

"Loading from FCLM..." stuck:
  - Make sure Firefox is open with an active Midway session
  - Visit https://fclm-portal.amazon.com in Firefox to refresh session
  - Restart the dashboard server

No X-Train dots showing:
  - Wait ~90 seconds after startup (permissions check runs in background)
  - Click "Refresh Now" to trigger a new check

No "CURRENTLY PICKING" section:
  - Make sure Rodeo is open with OB Pick Center v3.5+ Tampermonkey
  - Check Rodeo console (F12) for "[Dashboard Bridge] Pushed XX pickers"

Dashboard shows old data:
  - Use Ctrl+Shift+R to hard refresh
  - Or open in Incognito window

Port 8787 already in use:
  - Close any other terminal running the dashboard
  - Or change port in staffing_config.yaml under dashboard.port

============================================================
FOR DEVELOPERS:
============================================================

API Endpoints:
  GET  /api/data              - Current dashboard data (JSON)
  GET  /api/refresh           - Force immediate FCLM refresh
  GET  /api/status            - Server status
  GET  /api/history?employee_id=XXX  - Associate history
  POST /api/set-time-range    - Change time window
  POST /api/set-interval      - Change refresh interval
  POST /api/set-shift         - Switch Night/Day shift
  POST /api/recommend-move    - Get move recommendation
  POST /api/verify-permissions - Check one associate's perms
  POST /api/update-workforce  - Receive active picker data (from TM)

============================================================
  Built by ttuyen | HOU8 Pick Operations
============================================================
