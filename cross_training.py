"""
Cross-Training Parser - Verified Permissions Integration

Two-tier verification:
1. VERIFIED TRAINING: Specific certificates that directly grant permissions
2. FCLM PERMISSIONS: Actual active permissions in the system (ground truth)

Only shows cross-training badge if the associate has the verified training cert.
Optionally validates against FCLM permissions endpoint for absolute accuracy.
"""

import csv
import os
import glob
import logging
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)

# ============================================================
# VERIFIED Training Certificates (Layer 2 - grants permissions)
# ============================================================
# These are the specific certificates that, when earned, should
# result in the associate getting actual system permissions.

VERIFIED_CERT_MAP = {
    # Pick
    "PHL1 Pedestrian Pick - IAT": "Pick",
    "NACF TNS Pick Learning Plan Certificate": "Pick",
    "NACF TSSL Pick Learning Plan Certificate": "Pick",

    # Stow
    "PHL1 Pedestrian Stow": "Stow",
    "NACF TNS Stow Learning Plan Certificate": "Stow",
    "NACF TSSL Stow Learning Plan Certificate": "Stow",

    # Pack
    "NACF TNS Pack Learning Plan Certificate": "Pack",
    "NACF TSSL Singles/Multis Pack Learning Plan Certificate": "Pack",
    "NACF Pack Singles Beyond Day 1 Certificate": "Pack",

    # Receive
    "NACF TNS Each Receive Learning Plan Completion Certificate": "Receive",
    "NACF TNS Receive Decant Learning Plan Completion Certificate": "Receive",
    "NACF TNS Receive Pallet Traditional Learning Plan Completion Certificate": "Receive",
    "NACF TNS Receive Prep Learning Plan Completion Certificate": "Receive",
    "NACF TNS Receive Universal Learning Plan Certificate": "Receive",

    # Ship Dock
    "NACF ALL Container Builder Certificate": "Ship Dock",
    "NACF ARNS/TNS Ship Dock Certificate": "Ship Dock",

    # ICQA
    "NACF TNS SBC Learning Plan Certificate": "ICQA",
    "NACF TNS SRC Learning Plan Certificate": "ICQA",
    "NACF TNS CC Learning Plan Certificate": "ICQA",

    # VRets
    "NACF ALL Vrets Recycle and Remove Certificate": "VRets",
    "NACF ALL Vrets WHD Grading Certificate": "VRets",

    # IB Dock
    "NACF TNS Inbound Dock Trailer Unload": "IB Dock",
    "IPST TNS IB Dock": "IB Dock",
}

# ============================================================
# FCLM Permission Names (Layer 3 - ground truth)
# ============================================================
# If level is anything other than "NONE", the permission is active.

FCLM_PERMISSIONS = {
    "Pick": ["Pick Mech", "Pick Paper", "Pick Presort", "Pick RF"],
    "Pack": ["Slam At Pack", "PackApp", "PackAutomation", "SimplePackTool"],
    "Ship Dock": ["Outbound Dock"],
    "Stow": ["Stow to Prime (Sub)"],
    "ICQA": ["IC QA"],
    "Receive": ["Receive Case", "Receive Dock", "Receive Each", "Receive Support"],
}

# Department color mapping for the dashboard
DEPT_COLORS = {
    "Pick": "#4ade80",      # green
    "Stow": "#fbbf24",      # yellow
    "Receive": "#f472b6",   # pink
    "Pack": "#f87171",      # red
    "Ship Dock": "#1d4ed8", # dark blue
    "IB Dock": "#c084fc",   # purple
    "ICQA": "#2dd4bf",      # teal
    "VRets": "#e879f9",     # magenta
}


def find_training_csv(directory=None):
    """Find the most recent Certificate-tracking CSV file."""
    search_paths = []
    if directory:
        search_paths.append(directory)
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    search_paths.append(downloads)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search_paths.append(script_dir)

    for path in search_paths:
        pattern = os.path.join(path, "Certificate-tracking*.csv")
        matches = glob.glob(pattern)
        if matches:
            latest = max(matches, key=os.path.getmtime)
            logger.info("Found training CSV: %s", latest)
            return latest
    return None


def get_current_shift_prefix():
    """Determine which shift is currently active based on time of day."""
    hour = datetime.now().hour
    if hour >= 18 or hour < 5:
        # Night shift: 6:30PM - 5:00AM
        return ['N']
    elif hour >= 7 and hour < 18:
        # Day shift: 7:30AM - 6:00PM
        return ['D']
    else:
        # Transition window — include both
        return ['N', 'D']


def parse_cross_training(csv_path=None, shift_filter=True):
    """
    Parse verified cross-training data from the Certificate Tracking CSV.

    Uses VERIFIED_CERT_MAP (specific certs that grant permissions) instead
    of the broad IPST umbrella certs for accuracy.

    Returns:
        Tuple of (cross_training_dict, shift_info)
    """
    if csv_path is None:
        csv_path = find_training_csv()

    if csv_path is None or not os.path.exists(csv_path):
        logger.warning("No Certificate-tracking CSV found.")
        return {}, {"filtered": False, "employee_id_map": {}, "login_to_employee_id": {}}

    logger.info("Parsing cross-training data from: %s", csv_path)

    active_prefixes = get_current_shift_prefix() if shift_filter else None
    shift_name = "Night" if active_prefixes and 'N' in active_prefixes else "Day"

    cross_training = defaultdict(set)
    login_to_employee_id = {}
    total_rows = 0
    matched_rows = 0

    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)

        for row in reader:
            total_rows += 1

            status = row.get('Certificate Completion Status', '').strip()
            if status != 'Earned':
                continue

            emp_status = row.get('Employee Status', '').strip()
            if emp_status != 'Active':
                continue

            cert_title = row.get('Certificate Title', '').strip()
            login = row.get('AA Login', '').strip()
            shift_code = row.get('Shift Code', '').strip()
            employee_id = row.get('AA ID', '').strip()

            if not login or not cert_title:
                continue

            if login and employee_id:
                login_to_employee_id[login] = employee_id

            # Shift filter
            if active_prefixes and shift_code:
                shift_prefix = shift_code[0] if shift_code else ''
                if shift_prefix not in active_prefixes and shift_code not in ('FLEXPT', 'FLEXRT', 'ACCOM'):
                    continue

            # Use VERIFIED cert map only
            department = VERIFIED_CERT_MAP.get(cert_title)
            if department:
                cross_training[login].add(department)
                matched_rows += 1

    logger.info(
        "Cross-training (verified): %d associates on %s shift, %d matched rows (from %d total)",
        len(cross_training), shift_name, matched_rows, total_rows
    )

    dept_counts = defaultdict(int)
    for login, depts in cross_training.items():
        for dept in depts:
            dept_counts[dept] += 1

    for dept, count in sorted(dept_counts.items(), key=lambda x: -x[1]):
        logger.info("  %s: %d associates", dept, count)

    # Build employee_id -> departments reverse map
    employee_id_to_depts = {}
    for login, depts in cross_training.items():
        emp_id = login_to_employee_id.get(login)
        if emp_id:
            employee_id_to_depts[emp_id] = sorted(depts)

    shift_info = {
        "filtered": shift_filter,
        "shift_name": shift_name,
        "shift_prefixes": active_prefixes,
        "total_onsite": len(cross_training),
        "employee_id_map": employee_id_to_depts,
        "login_to_employee_id": login_to_employee_id,
    }

    return dict(cross_training), shift_info


def get_cross_training_summary(cross_training):
    """Get summary for the dashboard."""
    associates = {}
    dept_counts = defaultdict(int)

    for login, depts in cross_training.items():
        associates[login] = sorted(depts)
        for dept in depts:
            dept_counts[dept] += 1

    return {
        "associates": associates,
        "department_counts": dict(dept_counts),
        "department_colors": DEPT_COLORS,
    }


# ============================================================
# FCLM Permissions Checker (Layer 3)
# ============================================================

def check_fclm_permissions(employee_id, config):
    """
    Check actual FCLM permissions for an associate.

    Queries: /employee/permissions?employeeId=<id>&warehouseId=HOU8
    Parses the HTML to find active permissions (level != NONE).

    Returns: set of department names the associate has permissions for.
    """
    import requests
    from bs4 import BeautifulSoup
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fclm_rate_puller import get_midway_cookies

    url = (
        f"https://fclm-portal.amazon.com/employee/permissions"
        f"?employeeId={employee_id}"
        f"&warehouseId={config.get('warehouse_id', 'HOU8')}"
    )

    cookies = get_midway_cookies(config)
    if not cookies:
        return set()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        session = requests.Session()
        session.verify = False
        for name, value in cookies.items():
            session.cookies.set(name, value, domain="fclm-portal.amazon.com")
            session.cookies.set(name, value, domain="midway-auth.amazon.com")

        response = session.get(url, headers=headers, timeout=15)

        if response.status_code != 200:
            return set()

        return _parse_permissions_html(response.text)

    except Exception as e:
        logger.warning("Error checking permissions for %s: %s", employee_id, e)
        return set()


def batch_check_fclm_permissions(employee_ids, config):
    """
    Check FCLM permissions for multiple associates using a single session.
    
    Much faster than calling check_fclm_permissions for each one individually
    because it extracts cookies once and reuses the HTTP session.
    
    Returns: dict of employee_id -> sorted list of department names
    """
    import requests
    import urllib3
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fclm_rate_puller import get_midway_cookies
    
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    cookies = get_midway_cookies(config)
    if not cookies:
        logger.error("No cookies available for batch permissions check")
        return {}
    
    # Build a single reusable session
    session = requests.Session()
    session.verify = False
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    for name, value in cookies.items():
        session.cookies.set(name, value, domain="fclm-portal.amazon.com")
        session.cookies.set(name, value, domain="midway-auth.amazon.com")
    
    warehouse_id = config.get('warehouse_id', 'HOU8')
    results = {}
    
    for i, eid in enumerate(employee_ids):
        try:
            url = f"https://fclm-portal.amazon.com/employee/permissions?employeeId={eid}&warehouseId={warehouse_id}"
            response = session.get(url, timeout=15)
            
            if response.status_code == 200:
                perms = _parse_permissions_html(response.text)
                if perms:
                    results[eid] = sorted(perms)
            
            if (i + 1) % 10 == 0:
                logger.info("  Checked %d/%d associates...", i + 1, len(employee_ids))
                
        except Exception as e:
            logger.warning("  Failed for %s: %s", eid, e)
    
    return results


def _parse_permissions_html(html_content):
    """
    Parse the FCLM permissions HTML page to find active permissions.

    Looks for rows with permission levels that are not "NONE".
    Maps them to department names using FCLM_PERMISSIONS.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")
    active_depts = set()

    # The permissions page has a table with columns:
    # Department | Permission | Level | toggles...
    tables = soup.find_all("table")

    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue

            cell_texts = [c.get_text(strip=True) for c in cells]
            dept_name = cell_texts[0]
            perm_name = cell_texts[1] if len(cells) > 1 else ""
            level_raw = cell_texts[2] if len(cells) > 2 else "0NONE"

            # Parse level: format is "0NONE", "1BEGINNER", "2INTERMEDIATE", "3EXPERT"
            # or sometimes "No Training", plain "NONE", etc.
            # Active = first digit > 0, or contains BEGINNER/INTERMEDIATE/EXPERT
            level_str = level_raw.strip()

            is_active = False
            if level_str and level_str[0].isdigit():
                try:
                    level_num = int(level_str[0])
                    is_active = level_num > 0
                except ValueError:
                    is_active = False
            elif "BEGINNER" in level_str.upper() or "INTERMEDIATE" in level_str.upper() or "EXPERT" in level_str.upper():
                is_active = True

            if not is_active:
                continue

            # Map the permission to our department names
            for our_dept, perm_list in FCLM_PERMISSIONS.items():
                if perm_name in perm_list:
                    active_depts.add(our_dept)
                    break

    return active_depts


if __name__ == "__main__":
    """Run standalone to verify."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    csv_path = r"C:\Users\ttuyen\Downloads\Certificate-tracking_1784354649563.csv"

    print("=" * 60)
    print("VERIFIED CROSS-TRAINING (current shift):")
    print("=" * 60)
    cross_training, shift_info = parse_cross_training(csv_path, shift_filter=True)

    print(f"\nShift: {shift_info['shift_name']}")
    print(f"Total onsite cross-trained: {len(cross_training)}")

    dept_counts = defaultdict(int)
    for login, depts in cross_training.items():
        for dept in depts:
            dept_counts[dept] += 1

    print("\nDepartment breakdown:")
    for dept, count in sorted(dept_counts.items(), key=lambda x: -x[1]):
        print(f"  {dept}: {count}")

    print(f"\nSample (first 15):")
    for login, depts in list(cross_training.items())[:15]:
        print(f"  {login}: {', '.join(sorted(depts))}")
