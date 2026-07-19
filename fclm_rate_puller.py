"""
FCLM Rate Puller - Automated Process Path Rate Monitor

Two reports:
1. 15-Minute Interval Report: Pulls EACH-Total UPH per process path every
   15 minutes from the PPA Process Inspector.
2. End-of-Shift Report: Pulls EACH-Total UPH + hours staffed per process path
   from the PPA Process Inspector at 4:55 AM daily.

Supports automatic cookie extraction from Firefox or Chrome.
Firefox is recommended — no encryption, just authenticate and go.
"""

import json
import time
import logging
import sys
import re
import os
import shutil
import sqlite3
import base64
import glob as glob_module
import configparser
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
import yaml
from bs4 import BeautifulSoup
import urllib3

# Suppress SSL warnings since we're on an internal corporate network
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Windows-specific imports for Chrome cookie decryption
try:
    import win32crypt
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except ImportError:
    try:
        from Cryptodome.Cipher import AES
        HAS_CRYPTO = True
    except ImportError:
        HAS_CRYPTO = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# Configuration
# ============================================================

def load_config(config_path="config.yaml"):
    """Load configuration from YAML file."""
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        logger.info("Configuration loaded from %s", config_path)
        return config
    except FileNotFoundError:
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error("Error parsing config file: %s", e)
        sys.exit(1)


# ============================================================
# Firefox Cookie Extraction
# ============================================================

def get_firefox_profiles_path():
    """Get the Firefox profiles directory on Windows."""
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "Mozilla", "Firefox", "Profiles")


def get_firefox_default_profile(profiles_path=None):
    """
    Find the default Firefox profile directory.

    Reads profiles.ini to find the default profile, or falls back
    to finding the first *.default-release profile.
    """
    if profiles_path is None:
        profiles_path = get_firefox_profiles_path()

    # Try reading profiles.ini
    profiles_ini = os.path.join(os.path.dirname(profiles_path), "profiles.ini")
    if os.path.exists(profiles_ini):
        config = configparser.ConfigParser()
        config.read(profiles_ini)

        # Look for the default profile
        for section in config.sections():
            if section.startswith("Install") or section.startswith("Profile"):
                if config.has_option(section, "Default"):
                    profile_path = config.get(section, "Default")
                    # Could be relative or absolute
                    if os.path.isabs(profile_path):
                        if os.path.exists(profile_path):
                            return profile_path
                    else:
                        full_path = os.path.join(os.path.dirname(profiles_ini), profile_path)
                        if os.path.exists(full_path):
                            return full_path

    # Fallback: look for *.default-release or *.default profile directories
    if os.path.exists(profiles_path):
        for pattern in ["*.default-release", "*.default"]:
            matches = glob_module.glob(os.path.join(profiles_path, pattern))
            if matches:
                return matches[0]

    logger.error("Could not find Firefox default profile in: %s", profiles_path)
    return None


def extract_firefox_cookies(domain, profile_path=None):
    """
    Extract cookies for a given domain from Firefox's cookie database.

    Firefox stores cookies in plain text (no encryption!) in a SQLite database
    at <profile>/cookies.sqlite.

    Args:
        domain: The domain to extract cookies for (e.g., "amazon.com")
        profile_path: Path to Firefox profile directory (auto-detected if None)

    Returns:
        dict of cookie_name -> cookie_value
    """
    if profile_path is None:
        profile_path = get_firefox_default_profile()

    if profile_path is None:
        logger.error("Firefox profile not found. Please specify firefox_profile_path in config.")
        return {}

    cookie_db_path = os.path.join(profile_path, "cookies.sqlite")

    if not os.path.exists(cookie_db_path):
        logger.error("Firefox cookie database not found: %s", cookie_db_path)
        return {}

    # Firefox may lock the database, so copy to a temp file
    temp_db_path = os.path.join(os.environ.get("TEMP", "."), "firefox_cookies_temp.sqlite")
    try:
        shutil.copy2(cookie_db_path, temp_db_path)
    except PermissionError:
        logger.error(
            "Cannot access Firefox cookies. Firefox may have an exclusive lock. "
            "Try closing Firefox or the tool will retry next cycle."
        )
        return {}

    cookies = {}
    try:
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()

        # Firefox cookie table schema: name, value, host, path, expiry, ...
        # The 'value' column is plain text — no decryption needed!
        cursor.execute(
            "SELECT name, value, host FROM moz_cookies WHERE host LIKE ?",
            (f"%{domain}%",),
        )

        for name, value, host in cursor.fetchall():
            if value:
                cookies[name] = value

        conn.close()
        logger.info(
            "Extracted %d cookies for domain '%s' from Firefox", len(cookies), domain
        )
    except Exception as e:
        logger.error("Error reading Firefox cookie database: %s", e)
    finally:
        try:
            os.remove(temp_db_path)
        except OSError:
            pass

    return cookies


# ============================================================
# Chrome Cookie Extraction (Windows)
# ============================================================

def get_chrome_default_path():
    """Get the default Chrome user data directory on Windows."""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(local_app_data, "Google", "Chrome", "User Data")


def get_chrome_encryption_key(chrome_path):
    """
    Extract the AES encryption key from Chrome's Local State file.
    """
    local_state_path = os.path.join(chrome_path, "Local State")

    if not os.path.exists(local_state_path):
        logger.error("Chrome Local State file not found: %s", local_state_path)
        return None

    with open(local_state_path, "r", encoding="utf-8") as f:
        local_state = json.loads(f.read())

    encrypted_key_b64 = local_state["os_crypt"]["encrypted_key"]
    encrypted_key = base64.b64decode(encrypted_key_b64)
    encrypted_key = encrypted_key[5:]  # Remove "DPAPI" prefix

    if not HAS_WIN32:
        logger.error("pywin32 is required for Chrome cookie decryption.")
        return None

    decrypted_key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
    return decrypted_key


def decrypt_cookie_value(encrypted_value, key):
    """Decrypt a Chrome cookie value (AES-256-GCM or DPAPI)."""
    if not encrypted_value:
        return ""

    if encrypted_value[:3] in (b"v10", b"v20"):
        if not HAS_CRYPTO:
            logger.error("pycryptodome is required for Chrome cookie decryption.")
            return ""

        nonce = encrypted_value[3:15]
        ciphertext_with_tag = encrypted_value[15:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        ciphertext = ciphertext_with_tag[:-16]
        tag = ciphertext_with_tag[-16:]

        try:
            decrypted = cipher.decrypt_and_verify(ciphertext, tag)
            return decrypted.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as e:
            logger.warning("Failed to decrypt cookie: %s", e)
            return ""
    else:
        if HAS_WIN32:
            try:
                decrypted = win32crypt.CryptUnprotectData(
                    encrypted_value, None, None, None, 0
                )[1]
                return decrypted.decode("utf-8")
            except Exception as e:
                logger.warning("DPAPI decryption failed: %s", e)
                return ""
        return ""


def extract_chrome_cookies(domain, chrome_path=None, profile="Default"):
    """Extract cookies for a given domain from Chrome's cookie database."""
    if chrome_path is None:
        chrome_path = get_chrome_default_path()

    encryption_key = get_chrome_encryption_key(chrome_path)
    if encryption_key is None:
        return {}

    cookie_db_path = os.path.join(chrome_path, profile, "Network", "Cookies")
    if not os.path.exists(cookie_db_path):
        cookie_db_path = os.path.join(chrome_path, profile, "Cookies")
        if not os.path.exists(cookie_db_path):
            logger.error("Chrome cookie database not found")
            return {}

    temp_db_path = os.path.join(os.environ.get("TEMP", "."), "chrome_cookies_temp")
    try:
        shutil.copy2(cookie_db_path, temp_db_path)
    except PermissionError:
        logger.error("Cannot access Chrome cookies. Try closing Chrome.")
        return {}

    cookies = {}
    try:
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, encrypted_value, host_key FROM cookies WHERE host_key LIKE ?",
            (f"%{domain}%",),
        )

        for name, encrypted_value, host_key in cursor.fetchall():
            decrypted_value = decrypt_cookie_value(encrypted_value, encryption_key)
            if decrypted_value:
                cookies[name] = decrypted_value

        conn.close()
        logger.info("Extracted %d cookies for domain '%s' from Chrome", len(cookies), domain)
    except Exception as e:
        logger.error("Error reading Chrome cookie database: %s", e)
    finally:
        try:
            os.remove(temp_db_path)
        except OSError:
            pass

    return cookies


# ============================================================
# Cookie Dispatcher
# ============================================================

def get_midway_cookies(config):
    """
    Get Midway cookies using the configured method.

    Supports: firefox_auto (default), chrome_auto, manual
    """
    cookie_config = config.get("cookie_settings", {})
    method = cookie_config.get("method", "firefox_auto")

    # Pull cookies from multiple relevant domains
    domains = ["fclm-portal.amazon.com", "midway-auth.amazon.com", ".amazon.com"]

    raw_cookies = {}

    if method == "firefox_auto":
        logger.info("Extracting Midway cookies from Firefox...")
        profile_path = cookie_config.get("firefox_profile_path", None)
        for domain in domains:
            domain_cookies = extract_firefox_cookies(domain, profile_path)
            logger.info("  Domain '%s': found %d cookies", domain, len(domain_cookies))
            if domain_cookies:
                logger.info("  Cookie names: %s", list(domain_cookies.keys())[:20])
            raw_cookies.update(domain_cookies)

        if not raw_cookies:
            logger.warning("Firefox extraction failed. Trying fallback...")

    elif method == "chrome_auto":
        logger.info("Extracting Midway cookies from Chrome...")
        chrome_path = cookie_config.get("chrome_path", None)
        profile = cookie_config.get("chrome_profile", "Default")
        for domain in domains:
            domain_cookies = extract_chrome_cookies(domain, chrome_path, profile)
            raw_cookies.update(domain_cookies)

        if not raw_cookies:
            logger.warning("Chrome extraction failed. Trying fallback...")

    # Fallback: manual cookies from config
    if not raw_cookies:
        for cookie_str in config.get("midway_cookies", []):
            if "=" in cookie_str:
                name, value = cookie_str.split("=", 1)
                raw_cookies[name.strip()] = value.strip()

    if not raw_cookies:
        logger.error(
            "No cookies available. Please authenticate with Midway in your browser "
            "or add cookies manually to config.yaml"
        )
        return {}

    # Only keep cookies needed for FCLM authentication
    # The server rejects requests with too many cookies ("header too large")
    fclm_cookie_names = {
        "amzn_sso_token", "amzn_sso_rfp", "userMetricsProfile",
        "federate_id_token", "federate_id_token-0",
        "Federate_PROD_authN_MIDWAY", "Federate_PROD_authN_KERB",
        "session", "__Host-session", "amazon_enterprise_access",
        "fcl-token", "session-id", "session-token",
    }

    cookies = {k: v for k, v in raw_cookies.items() if k in fclm_cookie_names}

    if not cookies:
        logger.warning(
            "No known FCLM auth cookies found. Sending all %d cookies.",
            len(raw_cookies),
        )
        cookies = raw_cookies
    else:
        logger.info(
            "Sending %d cookies (filtered from %d)", len(cookies), len(raw_cookies),
        )

    return cookies


# ============================================================
# URL Builder
# ============================================================

def build_fclm_url(config, start_time=None, end_time=None):
    """Build the FCLM functionRollup URL for a 15-minute intraday window."""
    base_url = "https://fclm-portal.amazon.com/reports/functionRollup"

    if start_time is None or end_time is None:
        start_time, end_time = get_current_interval()

    # Build URL manually to avoid encoding slashes in dates
    start_date = start_time.strftime("%Y/%m/%d")
    end_date = end_time.strftime("%Y/%m/%d")

    url = (
        f"{base_url}?reportFormat=HTML"
        f"&warehouseId={config['warehouse_id']}"
        f"&processId={config['process_id']}"
        f"&maxIntradayDays=1"
        f"&spanType=Intraday"
        f"&startDateIntraday={start_date}"
        f"&startHourIntraday={start_time.hour}"
        f"&startMinuteIntraday={start_time.minute}"
        f"&endDateIntraday={end_date}"
        f"&endHourIntraday={end_time.hour}"
        f"&endMinuteIntraday={end_time.minute}"
    )

    logger.info("Built URL: %s", url)
    return url


def build_ppa_url(config, start_time=None, end_time=None):
    """
    Build the FCLM functionRollup URL for a 15-minute intraday window.

    Uses reportFormat=JSON to get structured data including per-path rates.
    The JSON response includes EACH-Total UPH for each process path, which
    matches the rate shown in the PPA detailed table's Total row.
    """
    base_url = "https://fclm-portal.amazon.com/reports/functionRollup"

    if start_time is None or end_time is None:
        start_time, end_time = get_current_interval()

    start_date = start_time.strftime("%Y/%m/%d")
    end_date = end_time.strftime("%Y/%m/%d")

    url = (
        f"{base_url}?reportFormat=HTML"
        f"&warehouseId={config['warehouse_id']}"
        f"&processId={config['process_id']}"
        f"&maxIntradayDays=1"
        f"&spanType=Intraday"
        f"&startDateIntraday={start_date}"
        f"&startHourIntraday={start_time.hour}"
        f"&startMinuteIntraday={start_time.minute}"
        f"&endDateIntraday={end_date}"
        f"&endHourIntraday={end_time.hour}"
        f"&endMinuteIntraday={end_time.minute}"
    )

    logger.info("Built URL: %s", url)
    return url


def get_current_interval():
    """Get the most recently completed 15-minute interval."""
    now = datetime.now()
    current_minute = now.minute
    rounded_minute = (current_minute // 15) * 15

    end_time = now.replace(minute=rounded_minute, second=0, microsecond=0)
    start_time = end_time - timedelta(minutes=15)

    logger.info("Interval: %s - %s", start_time.strftime("%H:%M"), end_time.strftime("%H:%M"))
    return start_time, end_time


# ============================================================
# Data Fetcher
# ============================================================

def fetch_fclm_data(url, config):
    """Fetch data from the FCLM portal using Midway cookies."""
    cookies = get_midway_cookies(config)

    if not cookies:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) "
                      "Gecko/20100101 Firefox/120.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        session = requests.Session()
        session.headers.update(headers)
        session.verify = False
        session.max_redirects = 10

        # Load all cookies into the session without domain restriction
        # so they're sent across SSO redirect hops
        for name, value in cookies.items():
            session.cookies.set(name, value, domain="fclm-portal.amazon.com")
            session.cookies.set(name, value, domain="midway-auth.amazon.com")

        response = session.get(url, timeout=30)
        response.raise_for_status()
        logger.info("Successfully fetched FCLM data (status %d)", response.status_code)
        return response.text
    except requests.exceptions.TooManyRedirects:
        logger.error(
            "Too many redirects — SSO authentication loop detected. "
            "Your Midway session may have expired. "
            "Please re-authenticate at https://fclm-portal.amazon.com in Firefox."
        )
        return None
    except requests.exceptions.HTTPError as e:
        if response.status_code in (401, 403):
            logger.error(
                "Authentication failed (HTTP %d). Your Midway session may have expired. "
                "Please re-authenticate at https://fclm-portal.amazon.com in your browser.",
                response.status_code,
            )
        else:
            logger.error("HTTP error fetching FCLM data: %s", e)
            logger.error("Response body: %s", response.text[:500])
        return None
    except requests.exceptions.RequestException as e:
        logger.error("Error fetching FCLM data: %s", e)
        return None


# ============================================================
# Parser
# ============================================================

def parse_json_rate_data(data):
    """
    Parse JSON response from functionRollup reportFormat=JSON.

    The JSON structure varies, but typically contains per-function/path entries
    with rate, volume, and hours data. We look for fields like:
    - 'functionName' or 'processPath' for the path name
    - 'rate' or 'uph' or 'unitsPerHour' for the rate
    - 'volume' or 'units' for the volume
    - 'hours' or 'hoursStaffed' or 'laborHours' for hours
    """
    results = []

    # The JSON might be a dict with a data array, or a direct array
    if isinstance(data, dict):
        # Look for common wrapper keys
        entries = (
            data.get("data")
            or data.get("functions")
            or data.get("results")
            or data.get("rows")
            or data.get("processPaths")
            or []
        )
        if not entries and isinstance(data, dict):
            # Maybe it's a single-level dict with path names as keys
            for key, value in data.items():
                if isinstance(value, dict):
                    entry = {"process_path": key}
                    for k, v in value.items():
                        kl = k.lower()
                        if "rate" in kl or "uph" in kl:
                            entry["rate"] = str(v)
                        elif "volume" in kl or "unit" in kl:
                            entry["units"] = str(v)
                        elif "hour" in kl:
                            entry["hours_staffed"] = str(v)
                    if entry.get("rate") or entry.get("units"):
                        results.append(entry)
    elif isinstance(data, list):
        entries = data
    else:
        return results

    for item in entries:
        if not isinstance(item, dict):
            continue

        entry = {}

        # Extract process path name
        for key in ("functionName", "processPath", "name", "function", "path"):
            if key in item:
                entry["process_path"] = str(item[key])
                break

        if not entry.get("process_path"):
            continue

        # Extract rate (prefer UPH/unitsPerHour over JPH)
        for key in ("unitsPerHour", "uph", "rate", "jph"):
            if key in item and item[key]:
                entry["rate"] = str(item[key])
                break

        # Extract volume
        for key in ("volume", "units", "totalUnits", "quantity"):
            if key in item and item[key]:
                entry["units"] = str(item[key])
                break

        # Extract hours
        for key in ("hours", "hoursStaffed", "laborHours", "totalHours"):
            if key in item and item[key]:
                entry["hours_staffed"] = str(item[key])
                break

        if entry.get("rate") or entry.get("units") or entry.get("hours_staffed"):
            results.append(entry)

    return results


def parse_rate_data(html_content):
    """
    Parse the functionRollup HTML response to extract EACH UPH per process path.

    The functionRollup page has a single summary table (#summary) where each
    process path is a group of 5 rows (Small, Medium, Large, HeavyBulky, Total).
    The path name is in a <th> with rowspan="5" containing an <a> tag.
    
    The "Total" row (class "size-total highlighted") for each path has <td> cells:
      td[0] = "Total" (size label)
      td[1] = Total Paid Hours
      td[2] = Jobs
      td[3] = JPH (jobs per hour)
      td[4] = EACH UNIT (volume)
      td[5] = EACH UPH (units per hour) ← this is what we want
    """
    if not html_content:
        return []

    # Try parsing as JSON first (reportFormat=JSON)
    content_stripped = html_content.strip()
    if content_stripped.startswith("{") or content_stripped.startswith("["):
        try:
            data = json.loads(content_stripped)
            results = parse_json_rate_data(data)
            if results:
                logger.info("Parsed %d process path entries from JSON", len(results))
                return results
            else:
                logger.warning("JSON parsed but no rate data found")
                with open("last_response_debug.json", "w", encoding="utf-8") as f:
                    f.write(content_stripped)
                logger.info("Saved JSON response to last_response_debug.json for inspection")
                return []
        except json.JSONDecodeError:
            logger.debug("Response is not valid JSON, trying HTML parser")

    soup = BeautifulSoup(html_content, "html.parser")
    results = []

    # Find the summary table
    summary_table = soup.find("table", id="summary")
    if not summary_table:
        # Fallback: find any result-table
        summary_table = soup.find("table", class_="result-table")
    if not summary_table:
        logger.warning("Could not find summary table in response")
        with open("last_response_debug.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        return []

    # Find all rows in the tbody that are visible (empl-all, not hidden)
    tbody = summary_table.find("tbody")
    if not tbody:
        logger.warning("No tbody in summary table")
        with open("last_response_debug.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        return []

    all_rows = tbody.find_all("tr")

    # Track the current process path name
    current_path = None

    for row in all_rows:
        # Skip hidden rows (temp/3rd party employee breakdowns)
        style = row.get("style", "")
        if "display: none" in style or "display:none" in style:
            continue

        # Only process "empl-all" rows (all employees combined)
        row_classes = row.get("class", [])
        if isinstance(row_classes, str):
            row_classes = row_classes.split()
        if "empl-all" not in row_classes:
            continue

        # Check if this row starts a new process path (has a <th> with <a>)
        th = row.find("th")
        if th:
            a_tag = th.find("a")
            if a_tag:
                current_path = a_tag.get_text(strip=True)

        # Check if this is the "Total" row for the current path
        td_cells = row.find_all("td")
        if not td_cells:
            continue

        # The Total row has "size-total" class on its cells
        first_td_classes = td_cells[0].get("class", [])
        if isinstance(first_td_classes, str):
            first_td_classes = first_td_classes.split()

        if "size-total" not in first_td_classes:
            continue

        # This is a Total row — extract the data
        # td[0] = "Total" label
        # td[1] = Total Paid Hours
        # td[2] = Jobs
        # td[3] = JPH
        # td[4] = EACH UNIT
        # td[5] = EACH UPH (the rate we want)
        if not current_path:
            continue

        entry = {"process_path": current_path}

        # Extract EACH UPH (index 5) — the rate from the detailed table
        if len(td_cells) > 5:
            rate_text = td_cells[5].get_text(strip=True)
            if rate_text:
                entry["rate"] = rate_text

        # Extract Total Paid Hours (index 1)
        if len(td_cells) > 1:
            hours_text = td_cells[1].get_text(strip=True)
            if hours_text:
                entry["hours_staffed"] = hours_text

        # Extract EACH UNIT / volume (index 4)
        if len(td_cells) > 4:
            units_text = td_cells[4].get_text(strip=True)
            if units_text:
                entry["units"] = units_text

        # Only include if we got meaningful data
        if entry.get("rate") or entry.get("units") or entry.get("hours_staffed"):
            results.append(entry)

    if results:
        logger.info("Parsed %d process path entries from PPA", len(results))
        # Temporarily save response for debugging column indices
        with open("last_response_debug.html", "w", encoding="utf-8") as f:
            f.write(html_content)
    else:
        # Check if this is a Midway auth redirect
        if "Midway Authentication" in html_content or "midway-auth" in html_content:
            logger.error(
                "Received Midway login page instead of data. "
                "Your session has expired. Please re-authenticate at "
                "https://fclm-portal.amazon.com in Firefox."
            )
        else:
            logger.warning(
                "No rate data found in response. The page structure may have changed."
            )
            with open("last_response_debug.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info("Saved response to last_response_debug.html for inspection")

    return results


def try_parse_json_data(soup, html_content):
    """Try to find and parse JSON data embedded in script tags."""
    results = []

    scripts = soup.find_all("script")
    for script in scripts:
        text = script.get_text()
        if "processPath" in text or "rate" in text.lower():
            json_matches = re.findall(r'(\[{.*?}\]|\{.*?"rate".*?\})', text, re.DOTALL)
            for match in json_matches:
                try:
                    data = json.loads(match)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                results.append(item)
                    elif isinstance(data, dict):
                        results.append(data)
                except json.JSONDecodeError:
                    continue

    data_elements = soup.find_all(attrs={"data-rate": True})
    for elem in data_elements:
        entry = {
            "process_path": elem.get("data-process-path", elem.get_text(strip=True)),
            "rate": elem.get("data-rate", ""),
        }
        if entry["rate"]:
            results.append(entry)

    return results


# ============================================================
# Slack Integration
# ============================================================

def format_slack_message(rate_data, config, start_time, end_time):
    """Format rate data into a Slack message."""
    warehouse_id = config["warehouse_id"]
    time_range = f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}"
    date_str = start_time.strftime("%Y-%m-%d")

    header = f":chart_with_upwards_trend: *FCLM Rate Report — {warehouse_id}*"
    subheader = f":clock3: {date_str} | {time_range}"

    lines = [header, subheader, ""]

    for entry in rate_data:
        path = entry.get("process_path", "Unknown")
        rate = entry.get("rate", "-") or "-"
        hours = entry.get("hours_staffed", "-") or "-"
        lines.append(f"*{path}* — Rate: {rate} | Hours: {hours}")

    return "\n".join(lines)


def post_to_slack(message, webhook_url):
    """Post a message to a Slack webhook/workflow trigger."""
    payload = {
        "Content": message,
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if response.status_code == 200:
            logger.info("Successfully posted to Slack")
            return True
        else:
            logger.error(
                "Slack webhook returned status %d: %s",
                response.status_code, response.text,
            )
            return False
    except requests.exceptions.RequestException as e:
        logger.error("Error posting to Slack: %s", e)
        return False


# ============================================================
# End-of-Shift Report (functionRollup)
# ============================================================

def build_end_of_shift_url(config):
    """
    Build the PPA Process Inspector URL for the full shift span.

    Uses the shift start/end hours from config (default: 18:00 - 06:00).
    Automatically calculates the correct dates for overnight shifts.
    """
    base_url = "https://fclm-portal.amazon.com/reports/functionRollup"

    eos_config = config.get("end_of_shift", {})
    shift_start_hour = eos_config.get("shift_start_hour", 18)
    shift_start_minute = eos_config.get("shift_start_minute", 0)
    shift_end_hour = eos_config.get("shift_end_hour", 6)
    shift_end_minute = eos_config.get("shift_end_minute", 0)
    process_id = eos_config.get("process_id", config.get("process_id", "1003001"))

    now = datetime.now()

    # For overnight shifts: start date is yesterday, end date is today
    if now.hour < 12:
        start_date = now - timedelta(days=1)
        end_date = now
    else:
        start_date = now
        end_date = now + timedelta(days=1)

    url = (
        f"{base_url}?reportFormat=HTML"
        f"&warehouseId={config['warehouse_id']}"
        f"&processId={process_id}"
        f"&maxIntradayDays=1"
        f"&spanType=Intraday"
        f"&startDateIntraday={start_date.strftime('%Y/%m/%d')}"
        f"&startHourIntraday={shift_start_hour}"
        f"&startMinuteIntraday={shift_start_minute}"
        f"&endDateIntraday={end_date.strftime('%Y/%m/%d')}"
        f"&endHourIntraday={shift_end_hour}"
        f"&endMinuteIntraday={shift_end_minute}"
    )

    logger.info("Built end-of-shift URL: %s", url)
    return url, start_date, end_date


def parse_function_rollup(html_content):
    """
    Parse the functionRollup HTML report to extract rate + hours staffed
    per process path.

    Returns a list of dicts: [{process_path, rate, hours_staffed}, ...]
    """
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, "html.parser")
    results = []

    tables = soup.find_all("table")

    for table in tables:
        # Get headers
        headers = []
        header_row = table.find("thead")
        if header_row:
            headers = [th.get_text(strip=True) for th in header_row.find_all("th")]
        else:
            first_row = table.find("tr")
            if first_row:
                headers = [cell.get_text(strip=True) for cell in first_row.find_all(["th", "td"])]

        if not headers:
            continue

        # Identify columns by header text
        col_map = {}
        for i, header in enumerate(headers):
            h = header.lower()
            if "rate" in h or "uph" in h or "units per hour" in h:
                col_map["rate"] = i
            elif "hour" in h and "staff" in h:
                col_map["hours_staffed"] = i
            elif "hour" in h and "staff" not in h and "rate" not in h:
                col_map["hours_staffed"] = i
            elif "labor hours" in h or "labourhours" in h:
                col_map["hours_staffed"] = i
            elif "function" in h or "path" in h or "process" in h:
                col_map["process_path"] = i
            elif "name" in h and "process_path" not in col_map:
                col_map["process_path"] = i

        # Need at least process path to be useful
        if "process_path" not in col_map:
            continue

        # Parse rows
        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")[1:]

        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if not cells:
                continue

            entry = {}
            for key, idx in col_map.items():
                if idx < len(cells):
                    entry[key] = cells[idx]

            if entry.get("process_path"):
                results.append(entry)

    if results:
        logger.info("Parsed %d process paths from functionRollup", len(results))
    else:
        logger.warning("No data found in functionRollup response.")
        with open("last_eos_response_debug.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info("Saved response to last_eos_response_debug.html for inspection")

    return results


def format_end_of_shift_message(rate_data, config, start_date, end_date):
    """Format end-of-shift data into a natural language Slack message."""
    warehouse_id = config["warehouse_id"]
    eos_config = config.get("end_of_shift", {})
    shift_start = f"{eos_config.get('shift_start_hour', 18):02d}:{eos_config.get('shift_start_minute', 0):02d}"
    shift_end = f"{eos_config.get('shift_end_hour', 6):02d}:{eos_config.get('shift_end_minute', 0):02d}"

    header = f":checkered_flag: *End-of-Shift Report — {warehouse_id}*"
    subheader = f":clock3: Shift: {start_date.strftime('%Y-%m-%d')} {shift_start} → {end_date.strftime('%Y-%m-%d')} {shift_end}"

    # Parse hours as floats for sorting and percentage calculation
    paths_with_hours = []
    for entry in rate_data:
        path = entry.get("process_path", "Unknown")
        rate = entry.get("rate", "-") or "-"
        try:
            hours = float(entry.get("hours_staffed", 0) or 0)
        except (ValueError, TypeError):
            hours = 0.0
        paths_with_hours.append({"path": path, "rate": rate, "hours": hours})

    # Sort by hours descending
    paths_with_hours.sort(key=lambda x: x["hours"], reverse=True)

    # Calculate total hours
    total_hours = sum(p["hours"] for p in paths_with_hours)

    if total_hours == 0:
        lines = [header, subheader, "", "No hours staffed data available for this shift."]
        return "\n".join(lines)

    # Split into top paths (>= 5% of total) and minor paths (< 5%)
    top_paths = []
    minor_paths = []
    for p in paths_with_hours:
        if p["hours"] <= 0:
            continue
        if (p["hours"] / total_hours) >= 0.05:
            top_paths.append(p)
        else:
            minor_paths.append(p)

    # Format top paths: "PathName (rate UPH, XX.XX hrs)"
    top_parts = []
    for p in top_paths:
        if p["rate"] and p["rate"] != "-":
            top_parts.append(f"{p['path']} ({p['rate']} UPH, {p['hours']:.2f} hrs)")
        else:
            top_parts.append(f"{p['path']} ({p['hours']:.2f} hrs)")

    # Build the message
    lines = [header, subheader, ""]

    if top_parts:
        top_str = ", ".join(top_parts[:-1])
        if len(top_parts) > 1:
            top_str += f", and {top_parts[-1]}"
        else:
            top_str = top_parts[0]
        lines.append(f"The top volume paths were {top_str}.")

    if minor_paths:
        minor_names = [p["path"] for p in minor_paths]
        minor_hours = sum(p["hours"] for p in minor_paths)
        minor_pct = (minor_hours / total_hours) * 100

        if len(minor_names) > 1:
            minor_str = ", ".join(minor_names[:-1]) + f", and {minor_names[-1]}"
        else:
            minor_str = minor_names[0]

        lines.append(
            f"The remaining paths — {minor_str} — collectively accounted for "
            f"{minor_pct:.0f}% of all Pick hours staffed."
        )

    # Add total shift rate for key process paths in bullet point format
    key_paths_ordered = ["MultiRelayPick", "OrderPickVNA", "RF Pick Singles"]
    # Build a lookup from all paths (case-insensitive matching)
    path_lookup = {}
    for p in paths_with_hours:
        path_lookup[p["path"].lower().strip()] = p

    bullet_lines = []
    for key_path in key_paths_ordered:
        matched = path_lookup.get(key_path.lower().strip())
        if matched:
            rate_str = matched["rate"] if matched["rate"] and matched["rate"] != "-" else "N/A"
            bullet_lines.append(f"• {key_path}: {rate_str} UPH")
        else:
            bullet_lines.append(f"• {key_path}: N/A")

    if bullet_lines:
        lines.append("")
        lines.append("*Total Shift Rate:*")
        lines.extend(bullet_lines)

    return "\n".join(lines)


def run_end_of_shift_report(config):
    """Run the end-of-shift report: fetch, parse, and post to Slack."""
    logger.info("=" * 60)
    logger.info("Running End-of-Shift Report")
    logger.info("=" * 60)

    url, start_date, end_date = build_end_of_shift_url(config)
    html = fetch_fclm_data(url, config)

    if not html:
        logger.error("Failed to fetch end-of-shift data")
        return False

    rate_data = parse_rate_data(html)

    if not rate_data:
        logger.warning("No end-of-shift data parsed, skipping Slack post")
        return False

    message = format_end_of_shift_message(rate_data, config, start_date, end_date)
    webhook_url = config.get("slack_webhook_url")

    if webhook_url:
        post_to_slack(message, webhook_url)
    else:
        logger.info("No Slack webhook configured. Output:\n%s", message)

    return True


# ============================================================
# Main Loop
# ============================================================

def run_once(config):
    """Run a single data pull and post cycle."""
    start_time, end_time = get_current_interval()

    url = build_ppa_url(config, start_time, end_time)
    html = fetch_fclm_data(url, config)

    if not html:
        logger.error("Failed to fetch data, skipping this interval")
        return False

    rate_data = parse_rate_data(html)

    if not rate_data:
        logger.warning("No rate data parsed, skipping Slack post")
        return False

    message = format_slack_message(rate_data, config, start_time, end_time)
    webhook_url = config.get("slack_webhook_url")

    if webhook_url:
        post_to_slack(message, webhook_url)
    else:
        logger.info("No Slack webhook configured. Output:\n%s", message)

    return True


def calculate_next_run(offset_minutes=10):
    """
    Calculate seconds until the next 15-minute boundary + offset.

    Default offset is 10 minutes to give the site time to finalize data.
    """
    now = datetime.now()
    current_minute = now.minute
    next_quarter = ((current_minute // 15) + 1) * 15

    if next_quarter >= 60:
        next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        next_run = now.replace(minute=next_quarter, second=0, microsecond=0)

    next_run += timedelta(minutes=offset_minutes)

    wait_seconds = (next_run - now).total_seconds()
    if wait_seconds < 0:
        wait_seconds += 900

    return wait_seconds, next_run


def should_run_end_of_shift(config):
    """
    Check if it's time to run the end-of-shift report.

    Returns True if the current time matches the configured trigger time
    (default: 4:55 AM) within a 15-minute window AFTER the trigger.
    This ensures the report still fires even if the script was restarted
    shortly after the trigger time.
    """
    eos_config = config.get("end_of_shift", {})
    if not eos_config.get("enabled", True):
        return False

    trigger_hour = eos_config.get("trigger_hour", 4)
    trigger_minute = eos_config.get("trigger_minute", 55)

    now = datetime.now()
    trigger_time = now.replace(hour=trigger_hour, minute=trigger_minute, second=0, microsecond=0)
    diff = (now - trigger_time).total_seconds()

    # Fire if we're between 0 and 15 minutes AFTER the trigger time.
    # This handles: normal operation, script restarts, and slow cycles.
    return 0 <= diff < 900  # within 15 minutes after trigger


def main():
    """Main entry point — runs the scheduler loop."""
    config_path = "config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    config = load_config(config_path)

    offset = config.get("schedule_offset_minutes", 10)
    browser = config.get("cookie_settings", {}).get("method", "firefox_auto")
    eos_config = config.get("end_of_shift", {})
    eos_time = f"{eos_config.get('trigger_hour', 4):02d}:{eos_config.get('trigger_minute', 55):02d}"

    logger.info("=" * 60)
    logger.info("FCLM Rate Puller Starting")
    logger.info("Warehouse: %s | Process: %s", config["warehouse_id"], config["process_id"])
    logger.info("15-min report: Every 15 minutes (offset: +%d min)", offset)
    logger.info("End-of-shift report: Daily at %s", eos_time)
    logger.info("Cookie source: %s", browser)
    logger.info("=" * 60)

    # Run 15-min report immediately on start
    logger.info("Running initial data pull...")
    run_once(config)

    # Track whether end-of-shift already ran this day
    eos_ran_today = False

    # Check if end-of-shift should fire immediately (handles script restart
    # during the trigger window, e.g., script crashed at 4:50 and restarted at 5:01)
    if should_run_end_of_shift(config):
        logger.info("End-of-shift trigger window is active on startup — running report now")
        run_end_of_shift_report(config)
        eos_ran_today = True

    # Then schedule every 15 minutes + check for end-of-shift
    while True:
        wait_seconds, next_run = calculate_next_run(offset)
        logger.info(
            "Next 15-min run at %s (in %.0f seconds)",
            next_run.strftime("%H:%M:%S"), wait_seconds,
        )

        # Sleep in small increments so we can check for end-of-shift trigger
        sleep_end = time.time() + wait_seconds
        while time.time() < sleep_end:
            # Reload config to pick up changes
            config = load_config(config_path)

            # Check if end-of-shift should run
            now = datetime.now()
            if should_run_end_of_shift(config) and not eos_ran_today:
                run_end_of_shift_report(config)
                eos_ran_today = True

            # Reset the daily flag at noon
            if now.hour == 12 and eos_ran_today:
                eos_ran_today = False

            # Sleep 30 seconds between checks
            remaining = sleep_end - time.time()
            time.sleep(min(30, max(0, remaining)))

        # Run the 15-min report
        config = load_config(config_path)
        run_once(config)


if __name__ == "__main__":
    main()
