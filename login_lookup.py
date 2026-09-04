"""
Login Lookup — resolve associate logins from FCLM in ONE call.

FCLM's Employee Roster page returns the whole FC's roster, one row per
associate, including both the employee_id and the login (alias). We fetch it
once and build a complete {employee_id: login} map for the site — no per-employee
requests, no dependency on name-matching the Picking Console feed.

    https://fclm-portal.amazon.com/employee/employeeRoster?warehouseId=<FC>&employeeId=<any>

Observed row layout (10 cells):
    [0] employee_id   e.g. 207016690
    [1] login/alias   e.g. ervenabl        <-- what we want
    [2] name          e.g. Venable,Eric
    [3] badge barcode
    [4] dept/cost center
    [5] hire date
    [6] type (TEMP/AMZN)
    [7] status (Active/...)
    [8] manager
    [9] (blank)

We resolve the login column positionally, with a value-shape fallback (a login
is alphanumeric with letters; employee_id/badge are all digits).
"""

import os
import re
import sys
import logging

logger = logging.getLogger(__name__)

_LOGIN_RE = re.compile(r"^[a-z][a-z0-9._-]{2,20}$", re.IGNORECASE)


def _looks_like_login(v):
    return bool(v) and not v.isdigit() and bool(_LOGIN_RE.match(v))


def fetch_roster_logins(config):
    """
    Fetch the FC employee roster and return { employee_id: login } for all rows.
    A single HTTP request covers the whole site.
    """
    import requests
    import urllib3
    from bs4 import BeautifulSoup
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fclm_rate_puller import get_midway_cookies

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    cookies = get_midway_cookies(config)
    if not cookies:
        logger.error("Login roster: no cookies available")
        return {}

    warehouse_id = config.get("warehouse_id", "HOU8")
    session = requests.Session()
    session.verify = False
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    for name, value in cookies.items():
        session.cookies.set(name, value, domain="fclm-portal.amazon.com")
        session.cookies.set(name, value, domain="midway-auth.amazon.com")

    url = (f"https://fclm-portal.amazon.com/employee/employeeRoster"
           f"?warehouseId={warehouse_id}&employeeId=0")
    try:
        r = session.get(url, timeout=45)
    except Exception as e:
        logger.warning("Login roster fetch failed: %s", e)
        return {}

    if r.status_code != 200:
        logger.warning("Login roster returned HTTP %s", r.status_code)
        return {}

    soup = BeautifulSoup(r.text, "html.parser")
    mapping = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 3:
            continue
        c0 = cells[0].get_text(strip=True)
        c1 = cells[1].get_text(strip=True)
        # Row must start with a numeric employee_id.
        if not c0.isdigit():
            continue
        # Login is normally cell[1]; fall back to the first login-shaped cell.
        login = c1 if _looks_like_login(c1) else ""
        if not login:
            for c in cells[1:4]:
                v = c.get_text(strip=True)
                if _looks_like_login(v):
                    login = v
                    break
        if login:
            mapping[c0] = login

    logger.info("Login roster: parsed %d employee->login rows for %s",
                len(mapping), warehouse_id)
    return mapping


def batch_fetch_logins(employee_ids, config):
    """
    Resolve logins for the given employee_ids using the full roster (one call).
    Returns { employee_id: login } limited to the requested ids that were found.
    """
    wanted = {str(e).strip() for e in (employee_ids or []) if str(e).strip()}
    if not wanted:
        return {}
    roster = fetch_roster_logins(config)
    if not roster:
        return {}
    resolved = {eid: roster[eid] for eid in wanted if eid in roster}
    logger.info("Login lookup: resolved %d/%d requested logins from roster",
                len(resolved), len(wanted))
    return resolved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fclm_rate_puller import load_config
    wh = sys.argv[1] if len(sys.argv) > 1 else "HOU8"
    test_eid = sys.argv[2] if len(sys.argv) > 2 else None
    cfg = load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"))
    cfg["warehouse_id"] = wh
    roster = fetch_roster_logins(cfg)
    print(f"roster size: {len(roster)}")
    if test_eid:
        print(f"{test_eid} -> {roster.get(test_eid)}")
    else:
        for k in list(roster)[:5]:
            print(f"  {k} -> {roster[k]}")
