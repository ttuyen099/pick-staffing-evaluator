"""
Staffing Dashboard Server - Live Auto-Refresh Dashboard

Runs a local web server that:
1. Serves the HTML dashboard
2. Polls FCLM data on a configurable interval (15/30 min)
3. Provides API endpoints for the dashboard to fetch data and change time ranges
4. Parses individual associate data from FCLM functionRollup detail tables

Does NOT modify the original fclm_rate_puller.py.
"""

import json
import re
import sys
import os
import time
import logging
import threading
import webbrowser
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import yaml
from bs4 import BeautifulSoup

# Add directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fclm_rate_puller import (
    load_config,
    get_midway_cookies,
    fetch_fclm_data,
    parse_rate_data,
)
from cross_training import parse_cross_training, get_cross_training_summary, DEPT_COLORS
from rate_history import record_shift_data, purge_old_data, get_associate_history, get_associate_path_averages, recommend_move, get_all_history_summary
from learning_engine import log_move_decision, auto_track_outcomes, auto_detect_moves, enhance_recommendation, get_learning_summary

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# Function ID to Path Name Mapping (fallback)
# ============================================================

FUNCTION_ID_TO_PATH = {
    "4300016817": "MultiRelayPick",
    "4300016815": "OrderPick SIOC",
    "4300016816": "OrderPickLowDensityP",
    "4300020902": "OrderPickVNA",
    "4300006870": "Orderpicker Pick",
    "4300000184": "RF Pick",
    "4300002523": "RF Pick Singles",
    "4300000707": "Giftwrap Picking",
}


# ============================================================
# Individual Associate Parser
# ============================================================

def parse_individual_associates(html_content):
    """
    Parse individual associate data from FCLM functionRollup detail tables.
    
    The HTML contains per-path detail tables with IDs like 'function-4300016816'.
    Each table has rows with associate-level performance data.
    
    Column layout (based on observed data):
        [0] Type (AMZN/TEMP)
        [1] Employee ID
        [2] Name (Last,First)
        [3] Manager
        [4] (empty or ItemPicked-related)
        [5] (empty or Jobs-related)
        [6] Paid Hours
        [7] (empty)
        [8] (empty or intermediate)
        [9] EACH-Total Units
        [10] EACH-Total UPH (THE RATE)
    
    Args:
        html_content: Raw HTML from the functionRollup report.
    
    Returns:
        dict: {path_name: [list of associate dicts]}
              Each associate dict has keys:
              employee_id, name, type, manager, paid_hours, units, rate
    """
    if not html_content:
        return {}
    
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Step 1: Build a mapping of function IDs to path names from the summary table.
    # The summary table has <th> elements containing <a href="#function-XXXXXXX">PathName</a>
    function_id_to_name = {}
    
    summary_table = soup.find("table", id="summary")
    if summary_table:
        # Find all <a> tags whose href starts with #function-
        for a_tag in summary_table.find_all("a", href=re.compile(r"^#function-\d+")):
            href = a_tag.get("href", "")
            # href is like "#function-4300016816"
            func_id = href.replace("#function-", "")
            path_name = a_tag.get_text(strip=True)
            if func_id and path_name:
                function_id_to_name[func_id] = path_name
    
    # Step 2: Find all detail tables with id matching 'function-XXXXXXX'
    associates_by_path = {}
    
    detail_tables = soup.find_all("table", id=re.compile(r"^function-\d+"))
    
    for table in detail_tables:
        table_id = table.get("id", "")
        func_id = table_id.replace("function-", "")
        
        # Resolve path name: prefer summary table mapping, fall back to hardcoded
        path_name = function_id_to_name.get(func_id) or FUNCTION_ID_TO_PATH.get(func_id, f"Unknown-{func_id}")
        
        # Parse rows from the table body
        tbody = table.find("tbody")
        if not tbody:
            continue
        
        associates = []
        rows = tbody.find_all("tr")
        
        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue
            
            # Extract text from all cells
            cell_texts = [cell.get_text(strip=True) for cell in cells]
            
            # Need at least 11 cells for the fields we want
            if len(cell_texts) < 4:
                continue
            
            # Skip aggregate/total rows - they typically have "Total" or empty Type
            emp_type = cell_texts[0] if len(cell_texts) > 0 else ""
            if emp_type not in ("AMZN", "TEMP"):
                continue
            
            employee_id = cell_texts[1] if len(cell_texts) > 1 else ""
            name = cell_texts[2] if len(cell_texts) > 2 else ""
            manager = cell_texts[3] if len(cell_texts) > 3 else ""
            
            # Column index 6 = Paid Hours
            paid_hours_str = cell_texts[6] if len(cell_texts) > 6 else ""
            # Column index 9 = EACH-Total Units
            units_str = cell_texts[9] if len(cell_texts) > 9 else ""
            # Column index 10 = EACH-Total UPH (rate)
            rate_str = cell_texts[10] if len(cell_texts) > 10 else ""
            
            # Parse numeric values
            try:
                paid_hours = float(paid_hours_str) if paid_hours_str else 0.0
            except ValueError:
                paid_hours = 0.0
            
            try:
                units = float(units_str) if units_str else 0.0
            except ValueError:
                units = 0.0
            
            try:
                rate = float(rate_str) if rate_str else 0.0
            except ValueError:
                rate = 0.0
            
            # Only include rows that have an employee ID
            if not employee_id:
                continue
            
            associate = {
                "employee_id": employee_id,
                "name": name,
                "type": emp_type,
                "manager": manager,
                "paid_hours": paid_hours,
                "units": units,
                "rate": rate,
            }
            associates.append(associate)
        
        if associates:
            associates_by_path[path_name] = associates
    
    logger.info(
        "Parsed individual associates: %d paths, %d total associates",
        len(associates_by_path),
        sum(len(v) for v in associates_by_path.values())
    )
    
    return associates_by_path


# ============================================================
# Configuration
# ============================================================

def load_all_config():
    """Load both main config and staffing config."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    config_path = os.path.join(script_dir, "config.yaml")
    config = load_config(config_path)
    
    eval_config_path = os.path.join(script_dir, "staffing_config.yaml")
    try:
        with open(eval_config_path, "r") as f:
            eval_config = yaml.safe_load(f)
    except FileNotFoundError:
        eval_config = {}
    
    return config, eval_config


# ============================================================
# Data Fetcher
# ============================================================

class DataManager:
    """Manages FCLM data fetching and caching."""
    
    def __init__(self, config, eval_config):
        self.config = config
        self.eval_config = eval_config
        self.current_data = None
        self.last_fetch_time = None
        self.raw_html = None  # Store raw HTML for associate parsing
        self.lock = threading.Lock()
        self._cached_permissions = {}  # employee_id -> permissions (refreshed in background)
        self.active_workforce = {}  # process_path -> list of active logins (from Tampermonkey)
        self.workforce_updated = None
        
        # Load cross-training data (filtered to current shift)
        self.cross_training, self.shift_info = parse_cross_training()
        self.cross_training_summary = get_cross_training_summary(self.cross_training)
        
        # Employee ID -> cross-training departments mapping
        self.employee_id_map = self.shift_info.get("employee_id_map", {})
        
        # Default time range: shift start to now
        self._set_default_time_range()
    
    def _set_default_time_range(self):
        """Set the default time range based on current shift."""
        now = datetime.now()
        
        if now.hour >= 18 or now.hour < 5:
            # Night shift: 6:30PM - 5:00AM
            if now.hour < 5:
                start_date = now - timedelta(days=1)
            else:
                start_date = now
            self.start_time = start_date.replace(hour=18, minute=30, second=0, microsecond=0)
        else:
            # Day shift: 7:30AM - 6:00PM
            self.start_time = now.replace(hour=7, minute=30, second=0, microsecond=0)
        
        self.end_time = now.replace(second=0, microsecond=0)
        # Round end time to nearest 15 min
        self.end_time = self.end_time.replace(minute=(self.end_time.minute // 15) * 15)
    
    def set_time_range(self, start_time, end_time):
        """Update the time range for data fetching."""
        self.start_time = start_time
        self.end_time = end_time
        logger.info("Time range updated: %s to %s", 
                   start_time.strftime("%Y/%m/%d %H:%M"),
                   end_time.strftime("%Y/%m/%d %H:%M"))
    
    def fetch_data(self):
        """Fetch rate data from FCLM for the current time range."""
        with self.lock:
            logger.info("Fetching FCLM data...")
            logger.info("  Range: %s to %s",
                       self.start_time.strftime("%Y/%m/%d %H:%M"),
                       self.end_time.strftime("%Y/%m/%d %H:%M"))
            
            url = self._build_url()
            html = fetch_fclm_data(url, self.config)
            
            if not html:
                logger.error("Failed to fetch FCLM data")
                return False
            
            # Store raw HTML for associate parsing
            self.raw_html = html
            
            rate_data = parse_rate_data(html)
            
            if not rate_data:
                logger.warning("No rate data parsed from response")
                return False
            
            # Build dashboard data structure
            self.current_data = self._build_dashboard_data(rate_data)
            self.last_fetch_time = datetime.now()
            
            # Record historical data and purge old entries
            if self.current_data.get("associates_by_path"):
                record_shift_data(self.current_data["associates_by_path"])
                auto_detect_moves(
                    self.current_data["associates_by_path"],
                    self.eval_config.get("rate_expectations", {}),
                    self.eval_config.get("default_rate_expectation", 5)
                )
                auto_track_outcomes(self.current_data["associates_by_path"])
                purge_old_data()
                # Refresh permissions in background (non-blocking)
                self._refresh_permissions_background()
            
            logger.info("Successfully fetched %d process paths", len(rate_data))
            return True
    
    def _refresh_permissions_background(self):
        """Refresh FCLM permissions for active associates in a background thread."""
        abp = self.current_data.get("associates_by_path", {})
        all_ids = set()
        for assocs in abp.values():
            for a in assocs:
                eid = a.get("employee_id", "")
                if eid:
                    all_ids.add(eid)
        
        if not all_ids:
            return
        
        def _do_check():
            logger.info("Background: verifying permissions for %d associates...", len(all_ids))
            from cross_training import batch_check_fclm_permissions
            result = batch_check_fclm_permissions(list(all_ids), self.config)
            self._cached_permissions = result
            logger.info("Background: permissions verified (%d associates)", len(result))
        
        threading.Thread(target=_do_check, daemon=True).start()
    
    def _build_url(self):
        """Build the functionRollup URL for the current time range."""
        base_url = "https://fclm-portal.amazon.com/reports/functionRollup"
        
        # Clamp end time to now - can't query future data
        now = datetime.now().replace(second=0, microsecond=0)
        now = now.replace(minute=(now.minute // 15) * 15)
        effective_end = min(self.end_time, now)
        
        # If start is in the future too, use start = end (empty range)
        effective_start = self.start_time
        if effective_start > effective_end:
            effective_end = effective_start
        
        start_date = effective_start.strftime("%Y/%m/%d")
        end_date = effective_end.strftime("%Y/%m/%d")
        
        url = (
            f"{base_url}?reportFormat=HTML"
            f"&warehouseId={self.config['warehouse_id']}"
            f"&processId={self.config['process_id']}"
            f"&maxIntradayDays=1"
            f"&spanType=Intraday"
            f"&startDateIntraday={start_date}"
            f"&startHourIntraday={effective_start.hour}"
            f"&startMinuteIntraday={effective_start.minute}"
            f"&endDateIntraday={end_date}"
            f"&endHourIntraday={effective_end.hour}"
            f"&endMinuteIntraday={effective_end.minute}"
        )
        
        return url
    
    def _build_dashboard_data(self, rate_data):
        """Build the dashboard data structure from parsed rate data."""
        rate_expectations = self.eval_config.get("rate_expectations", {})
        default_rate = self.eval_config.get("default_rate_expectation", 5)
        eval_settings = self.eval_config.get("staffing_evaluator", {})
        tolerance = eval_settings.get("tolerance", 0.05)
        dashboard_settings = self.eval_config.get("dashboard", {})
        refresh_interval = dashboard_settings.get("refresh_interval_minutes", 15)
        
        paths = {}
        for entry in rate_data:
            path_name = entry.get("process_path", "Unknown")
            try:
                rate = float(entry.get("rate", 0) or 0)
            except (ValueError, TypeError):
                rate = 0.0
            try:
                hours = float(entry.get("hours_staffed", 0) or 0)
            except (ValueError, TypeError):
                hours = 0.0
            try:
                units = int(float(entry.get("units", 0) or 0))
            except (ValueError, TypeError):
                units = 0
            
            paths[path_name] = {
                "rate": rate,
                "hours_staffed": hours,
                "units": units,
                "expected_rate": rate_expectations.get(path_name, default_rate),
            }
        
        # Parse individual associate data from raw HTML
        associates_by_path = {}
        if self.raw_html:
            associates_by_path = parse_individual_associates(self.raw_html)
        
        # Enrich associates with login from cross-training CSV reverse map
        # Build employee_id -> login reverse map from CSV + workforce bridge
        login_to_eid = self.shift_info.get("login_to_employee_id", {})
        eid_to_login = {v: k for k, v in login_to_eid.items()}
        
        # Also build login lookup from active workforce (Tampermonkey provides logins directly)
        # workforce: {path: [logins]} - match by checking if associate name appears in workforce
        
        for path_name, assocs in associates_by_path.items():
            for a in assocs:
                eid = a.get("employee_id", "")
                login = eid_to_login.get(eid, "")
                if not login:
                    # Try matching from workforce data by checking active list
                    wf_logins = self.active_workforce.get(path_name, [])
                    # Can't match by name easily, so just leave empty - workforce bridge handles it
                a["login"] = login
        
        # Use cached permissions (populated by background thread)
        verified_permissions = self._cached_permissions
        
        return {
            "warehouse_id": self.config.get("warehouse_id", "HOU8"),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "start_time": self.start_time.strftime("%Y-%m-%dT%H:%M"),
            "end_time": self.end_time.strftime("%Y-%m-%dT%H:%M"),
            "tolerance": tolerance,
            "refresh_interval_minutes": refresh_interval,
            "rate_expectations": rate_expectations,
            "default_rate_expectation": default_rate,
            "paths": paths,
            "associates_by_path": associates_by_path,
            "cross_training": self.cross_training_summary,
            "cross_training_by_employee_id": verified_permissions,
            "history_summary": get_all_history_summary(),
            "active_workforce": self.active_workforce,
            "eid_to_login": {v: k for k, v in self.shift_info.get("login_to_employee_id", {}).items()},
        }
    
    def get_data(self):
        """Get the current cached data."""
        return self.current_data


# ============================================================
# Polling Thread
# ============================================================

class Poller:
    """Background thread that polls FCLM data on an interval."""
    
    def __init__(self, data_manager, interval_minutes=15):
        self.data_manager = data_manager
        self.interval_minutes = interval_minutes
        self.running = False
        self.thread = None
    
    def start(self):
        """Start the polling thread."""
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Poller started: refreshing every %d minutes", self.interval_minutes)
    
    def stop(self):
        """Stop the polling thread."""
        self.running = False
    
    def set_interval(self, minutes):
        """Update the polling interval."""
        self.interval_minutes = minutes
        logger.info("Polling interval updated to %d minutes", minutes)
    
    def _run(self):
        """Polling loop."""
        while self.running:
            # Sleep first (initial fetch already done in main)
            sleep_seconds = self.interval_minutes * 60
            for _ in range(int(sleep_seconds / 5)):
                if not self.running:
                    return
                time.sleep(5)
            
            # Update end time to now before each fetch
            now = datetime.now().replace(second=0, microsecond=0)
            now = now.replace(minute=(now.minute // 15) * 15)
            self.data_manager.end_time = now
            
            # Fetch data
            self.data_manager.fetch_data()


# ============================================================
# HTTP Server with API
# ============================================================

# Global references (set in main)
data_manager = None
poller = None
backfill_status = {"state": "idle", "progress": ""}

class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for the dashboard server."""
    
    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass
    
    def _set_headers(self, content_type="application/json", status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self._set_headers()
    
    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        
        # API Endpoints
        if path == "/api/data":
            self._handle_get_data()
        elif path == "/api/refresh":
            self._handle_refresh()
        elif path == "/api/status":
            self._handle_status()
        elif path.startswith("/api/history"):
            self._handle_history()
        # Static file serving
        elif path == "/" or path == "/index.html":
            self._serve_file("staffing_dashboard.html", "text/html")
        elif path.endswith(".html"):
            self._serve_file(path.lstrip("/"), "text/html")
        elif path.endswith(".json"):
            self._serve_file(path.lstrip("/"), "application/json")
        elif path.endswith(".css"):
            self._serve_file(path.lstrip("/"), "text/css")
        elif path.endswith(".js"):
            self._serve_file(path.lstrip("/"), "application/javascript")
        else:
            self.send_error(404)
    
    def do_POST(self):
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == "/api/set-time-range":
            self._handle_set_time_range()
        elif path == "/api/set-interval":
            self._handle_set_interval()
        elif path == "/api/set-shift":
            self._handle_set_shift()
        elif path == "/api/verify-permissions":
            self._handle_verify_permissions()
        elif path == "/api/recommend-move":
            self._handle_recommend_move()
        elif path == "/api/update-workforce":
            self._handle_update_workforce()
        elif path == "/api/run-update":
            self._handle_run_update()
        else:
            self.send_error(404)
    
    def _handle_get_data(self):
        """Return current dashboard data as JSON."""
        current = data_manager.get_data()
        if current:
            try:
                # Always inject latest workforce data
                current["active_workforce"] = data_manager.active_workforce
                # Add permissions loading status
                current["xtrain_status"] = "ready" if len(data_manager._cached_permissions) > 0 else "loading"
                current["xtrain_count"] = len(data_manager._cached_permissions)
                # Add backfill status
                current["backfill_status"] = backfill_status.get("state", "unknown")
                current["backfill_progress"] = backfill_status.get("progress", "")
                payload = json.dumps(current)
                self._set_headers("application/json")
                self.wfile.write(payload.encode())
            except (TypeError, ValueError) as e:
                logger.error("JSON serialization error: %s", e)
                self._set_headers("application/json", 500)
                self.wfile.write(json.dumps({"error": f"Data serialization error: {e}"}).encode())
        else:
            self._set_headers("application/json", 503)
            self.wfile.write(json.dumps({"error": "No data available yet"}).encode())
    
    def _handle_refresh(self):
        """Force an immediate data refresh."""
        # Update end time to now
        now = datetime.now().replace(second=0, microsecond=0)
        now = now.replace(minute=(now.minute // 15) * 15)
        data_manager.end_time = now
        
        success = data_manager.fetch_data()
        self._set_headers("application/json")
        result = {"success": success, "timestamp": datetime.now().strftime("%H:%M:%S")}
        if success:
            result["data"] = data_manager.get_data()
        self.wfile.write(json.dumps(result).encode())
    
    def _handle_status(self):
        """Return server status."""
        self._set_headers("application/json")
        status = {
            "running": True,
            "last_fetch": data_manager.last_fetch_time.strftime("%Y-%m-%d %H:%M:%S") if data_manager.last_fetch_time else None,
            "polling_interval": poller.interval_minutes if poller else None,
            "start_time": data_manager.start_time.strftime("%Y-%m-%dT%H:%M"),
            "end_time": data_manager.end_time.strftime("%Y-%m-%dT%H:%M"),
        }
        self.wfile.write(json.dumps(status).encode())
    
    def _handle_set_time_range(self):
        """Handle POST to set a new time range and re-fetch data."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()
        
        try:
            params = json.loads(body)
            start_str = params.get("start_time")  # "2026-07-17T18:00"
            end_str = params.get("end_time")      # "2026-07-18T04:30"
            
            start_time = datetime.strptime(start_str, "%Y-%m-%dT%H:%M")
            end_time = datetime.strptime(end_str, "%Y-%m-%dT%H:%M")
            
            data_manager.set_time_range(start_time, end_time)
            success = data_manager.fetch_data()
            
            self._set_headers("application/json")
            result = {"success": success}
            if success:
                result["data"] = data_manager.get_data()
            self.wfile.write(json.dumps(result).encode())
            
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            self._set_headers("application/json", 400)
            self.wfile.write(json.dumps({"error": str(e)}).encode())
    
    def _handle_set_interval(self):
        """Handle POST to change the polling interval."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()
        
        try:
            params = json.loads(body)
            minutes = int(params.get("interval_minutes", 15))
            if minutes < 1:
                minutes = 1
            if minutes > 60:
                minutes = 60
            
            poller.set_interval(minutes)
            
            self._set_headers("application/json")
            self.wfile.write(json.dumps({"success": True, "interval": minutes}).encode())
        except (json.JSONDecodeError, ValueError) as e:
            self._set_headers("application/json", 400)
            self.wfile.write(json.dumps({"error": str(e)}).encode())
    
    def _handle_set_shift(self):
        """Set the active shift (night/day) — updates cross-training filter and time range."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()
        
        try:
            params = json.loads(body)
            shift = params.get("shift", "night")
            
            now = datetime.now()
            
            if shift == "night":
                # Night: 6:30PM - 5:00AM
                if now.hour < 5 or (now.hour == 5 and now.minute == 0):
                    start = (now - timedelta(days=1)).replace(hour=18, minute=30, second=0, microsecond=0)
                else:
                    start = now.replace(hour=18, minute=30, second=0, microsecond=0)
                end = now.replace(second=0, microsecond=0, minute=(now.minute // 15) * 15)
            else:
                # Day: 7:30AM - 6:00PM
                start = now.replace(hour=7, minute=30, second=0, microsecond=0)
                end = now.replace(second=0, microsecond=0, minute=(now.minute // 15) * 15)
            
            data_manager.set_time_range(start, end)
            
            # Reload cross-training with new shift filter
            shift_prefixes = ['N'] if shift == 'night' else ['D']
            from cross_training import parse_cross_training, get_cross_training_summary
            data_manager.cross_training, data_manager.shift_info = parse_cross_training(shift_filter=True)
            data_manager.cross_training_summary = get_cross_training_summary(data_manager.cross_training)
            data_manager.employee_id_map = data_manager.shift_info.get("employee_id_map", {})
            
            # Re-fetch data
            success = data_manager.fetch_data()
            
            self._set_headers("application/json")
            result = {"success": success, "shift": shift}
            if success:
                result["data"] = data_manager.get_data()
            self.wfile.write(json.dumps(result).encode())
        except Exception as e:
            self._set_headers("application/json", 500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())
    
    def _handle_verify_permissions(self):
        """Verify actual FCLM permissions for a specific associate."""
        from cross_training import check_fclm_permissions
        
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()
        
        try:
            params = json.loads(body)
            employee_id = params.get("employee_id", "")
            login = params.get("login", "")
            lookup = login or employee_id
            
            if not lookup:
                self._set_headers("application/json", 400)
                self.wfile.write(json.dumps({"error": "Provide employee_id or login"}).encode())
                return
            
            perms = check_fclm_permissions(lookup, data_manager.config)
            
            self._set_headers("application/json")
            self.wfile.write(json.dumps({
                "success": True,
                "employee_id": employee_id,
                "login": login,
                "verified_permissions": sorted(perms),
            }).encode())
        except Exception as e:
            self._set_headers("application/json", 500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())
    
    def _handle_history(self):
        """Get historical rate data for an associate."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        employee_id = params.get("employee_id", [None])[0]
        
        if not employee_id:
            self._set_headers("application/json", 400)
            self.wfile.write(json.dumps({"error": "Provide employee_id query param"}).encode())
            return
        
        history = get_associate_history(employee_id)
        averages = get_associate_path_averages(employee_id)
        
        self._set_headers("application/json")
        self.wfile.write(json.dumps({
            "employee_id": employee_id,
            "history": history,
            "path_averages": averages,
        }).encode())
    
    def _handle_recommend_move(self):
        """Get a move recommendation for an associate, enhanced with learning."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()
        
        try:
            params = json.loads(body)
            employee_id = params.get("employee_id", "")
            from_path = params.get("from_path", "")
            to_path = params.get("to_path", "")
            goal_uph = float(params.get("goal_uph", 0))
            log_decision = params.get("log_decision", False)
            
            if not employee_id or not from_path or not to_path or goal_uph <= 0:
                self._set_headers("application/json", 400)
                self.wfile.write(json.dumps({"error": "Provide employee_id, from_path, to_path, goal_uph"}).encode())
                return
            
            # Get base recommendation from history
            rec = recommend_move(employee_id, from_path, to_path, goal_uph)
            
            # Enhance with learning engine data
            rec = enhance_recommendation(rec, from_path, to_path, employee_id)
            
            # Add learning summary
            rec["learning_stats"] = get_learning_summary()
            
            # If user is confirming the move, log it
            if log_decision:
                login = params.get("login", "")
                name = params.get("name", "")
                from_rate = float(params.get("from_rate", 0))
                move_id = log_move_decision(
                    employee_id, login, name, from_path, to_path,
                    from_rate, goal_uph, rec["verdict"], rec["confidence"]
                )
                rec["move_logged"] = True
                rec["move_id"] = move_id
            
            self._set_headers("application/json")
            self.wfile.write(json.dumps({"success": True, "recommendation": rec}).encode())
        except Exception as e:
            self._set_headers("application/json", 500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())
    
    def _handle_update_workforce(self):
        """
        Receive active picker data from Tampermonkey script.
        
        Expects JSON: { "pickers": [ { "login", "name", "processPath", "active" }, ... ] }
        Stores in data_manager for the dashboard to use as a filter.
        """
        # Picking Console path names -> FCLM path names
        PATH_MAP = {
            'PPSingleFloor': 'RF Pick Singles',
            'PPSingleOPVNA': 'OrderPickVNA',
            'PPSingleOP': 'Orderpicker Pick',
            'PPSingleOPBOD': 'OrderPickLowDensityP',
            'PPSingleOPNonCon': 'OrderPickLowDensityP',
            'PPSingleSSD': 'OrderPick SIOC',
            'PPMultiBldgWide': 'MultiRelayPick',
            'PPMultiSSD': 'MultiRelayPick',
            'PPMezzPickSSD': 'OrderPick SIOC',
            'PPSingleGiftwrap': 'Giftwrap Picking',
            'PPSingleWrap': 'Giftwrap Picking',
            'PPSingleRFPick': 'RF Pick',
            'PPSingleMCF': 'Giftwrap Picking',
            'PPPalletPick': 'Pallet Pick',
            'PPHOVAuto': 'Pallet Pick',
            'PPHOVBOD': 'Pallet Pick',
            'PPHOVNonCon': 'Pallet Pick',
            'PPTeamlift': 'Teamlift Pick',
        }
        
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()
        
        try:
            payload = json.loads(body)
            pickers = payload.get("pickers", [])
            
            # Paths to exclude (not pick paths)
            EXCLUDE_PATHS = {'PPTrans', 'PPQA', 'PPTransOut', 'PPTransIn', 'PPICQA', 'PPCount', 'PPRebinHotpick', 'PPRebin'}
            
            # Build picker set: { fclm_path: list of logins } - includes ALL assigned pickers
            active_by_path = {}
            for p in pickers:
                pp_raw = p.get("processPath", "")
                if pp_raw in EXCLUDE_PATHS or pp_raw.startswith('PPTrans') or pp_raw.startswith('PPQA'):
                    continue
                pp = PATH_MAP.get(pp_raw, pp_raw)  # Map to FCLM name
                login = p.get("userId") or p.get("login", "")
                if pp and login:
                    if pp not in active_by_path:
                        active_by_path[pp] = []
                    active_by_path[pp].append(login)
            
            data_manager.active_workforce = active_by_path
            data_manager.workforce_updated = datetime.now()
            
            total_active = sum(len(v) for v in active_by_path.values())
            logger.info("Workforce updated: %d active pickers across %d paths", total_active, len(active_by_path))
            
            self._set_headers("application/json")
            self.wfile.write(json.dumps({"success": True, "active_count": total_active}).encode())
        except Exception as e:
            self._set_headers("application/json", 500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())
    
    def _handle_run_update(self):
        """Download latest files from GitHub and restart server."""
        try:
            import subprocess
            script_dir = os.path.dirname(os.path.abspath(__file__))
            updater_path = os.path.join(script_dir, "updater.py")
            python_exe = sys.executable
            
            # Run updater
            result = subprocess.run(
                [python_exe, updater_path],
                cwd=script_dir,
                capture_output=True, text=True, timeout=30
            )
            
            self._set_headers("application/json")
            self.wfile.write(json.dumps({"success": True, "output": result.stdout}).encode())
            
            # Schedule restart after response is sent
            import threading
            def restart():
                import time
                time.sleep(2)
                server_path = os.path.join(script_dir, "staffing_dashboard_server.py")
                subprocess.Popen([python_exe, server_path, "--no-browser"], cwd=script_dir)
                os._exit(0)
            threading.Thread(target=restart, daemon=True).start()
            threading.Thread(target=restart, daemon=True).start()
            
        except Exception as e:
            self._set_headers("application/json", 500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())
    
    def _serve_file(self, filename, content_type):
        """Serve a static file from the script directory."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        filepath = os.path.join(script_dir, filename)
        
        if not os.path.exists(filepath):
            self.send_error(404)
            return
        
        with open(filepath, "rb") as f:
            content = f.read()
        
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content)


# ============================================================
# Main
# ============================================================

def main():
    global data_manager, poller
    
    print("\n" + "=" * 60)
    print("  STAFFING DASHBOARD SERVER")
    print("  Live Auto-Refresh Dashboard for Pick Operations")
    print("=" * 60)
    
    # Load config
    config, eval_config = load_all_config()
    
    dashboard_settings = eval_config.get("dashboard", {})
    port = dashboard_settings.get("port", 8787)
    refresh_interval = dashboard_settings.get("refresh_interval_minutes", 15)
    
    print(f"\n  Warehouse: {config.get('warehouse_id', 'HOU8')}")
    print(f"  Refresh Interval: {refresh_interval} minutes")
    print(f"  Dashboard: http://localhost:{port}")
    print()
    
    # Initialize data manager
    data_manager = DataManager(config, eval_config)
    
    # Start HTTP server FIRST so the dashboard page can load immediately
    server = HTTPServer(("localhost", port), DashboardHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    url = f"http://localhost:{port}"
    print(f"\n  Dashboard running at: {url}")
    print(f"  Auto-refresh: every {refresh_interval} minutes")
    
    # Open browser (skip if restarting from update)
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    
    # Now do initial data fetch (browser will retry until ready)
    print("  Performing initial data fetch...")
    success = data_manager.fetch_data()
    if success:
        print("  Initial fetch successful!")
    else:
        print("  Initial fetch failed - dashboard will retry on next interval.")
        print("  (Make sure your Midway session is active in Firefox)")
    
    # Backfill historical data in background (catches up on missed days)
    global backfill_status
    backfill_status = {"state": "starting", "progress": ""}
    
    def run_backfill():
        from rate_history import backfill_history
        try:
            backfill_status["state"] = "running"
            backfill_status["progress"] = "Checking missing dates..."
            backfill_history(config, days=14)
            backfill_status["state"] = "done"
            backfill_status["progress"] = "Up to date"
        except Exception as e:
            backfill_status["state"] = "error"
            backfill_status["progress"] = str(e)
    
    threading.Thread(target=run_backfill, daemon=True).start()
    print("  Historical backfill running in background...")
    
    # Start poller
    poller = Poller(data_manager, refresh_interval)
    poller.start()
    
    print("  Press Ctrl+C to stop.\n")
    
    # Keep running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        poller.stop()
        server.shutdown()
        print("  Done.")


if __name__ == "__main__":
    main()
