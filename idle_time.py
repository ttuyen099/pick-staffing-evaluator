"""
Idle Time Scraper — flag associates with a long continuous idle gap.

Pulls each associate's FCLM "Employee Time Details" (timeDetails) report and
measures idle time, defined strictly as:

    A continuous span of paid, on-clock time that is NOT covered by a Direct
    process-path segment (e.g. "Pick<SEP>Orderpicker Pick").

Indirect / training / meeting / exempt / leave segments do NOT count as idle
and do NOT bridge a gap — that time is excised from the idle calculation
entirely. In other words, idle is only measured while the associate is working
(or should be working) a Direct path, never during training/indirect roles.

An associate is FLAGGED when their single longest continuous idle gap is at or
above the threshold (default 30 minutes).

Output per associate is intentionally minimal: login + idle minutes + flag.

----------------------------------------------------------------------------
FCLM timeDetails page structure (observed; see probe_idle.py to re-verify):

  Header "Employee Info" block:
      Login, Empl ID, Badge, Dept ID, Location, Manager, Status, Shift,
      Clock Status, SSpot Status, Job Level

  "Employee Time Details" table — columns:
      title | start | end | duration

  Example rows:
      OffClock/UnPaid        10/10-6:30:00 PM  10/10-6:32:00 PM  02:00
      OnClock/Paid           10/10-6:32:00 PM  10/11-5:00:00 AM  61:08
      Pick<SEP>Orderpicker Pick  10/10-6:32:00 PM  10/10-7:18:29 PM  46:29
      Pick<SEP>Orderpicker Pick  10/10-7:18:29 PM  10/10-7:25:13 PM  06:44
      Pick<SEP>Orderpicker Pick  10/10-7:25:13 PM  (blank)           07:55

  Notes:
   * The "duration" column format is unreliable across row types, so we compute
     durations from start/end timestamps. The duration column is only used as a
     fallback for an open/current segment (blank end -> measured up to "now").
   * "OnClock/Paid" is the paid frame. "OffClock/*" rows are not paid.
   * Direct rows carry a process<SEP>function title (e.g. "Pick<SEP>Orderpicker
     Pick"). Indirect/training/meeting/exempt/leave rows are classified as
     indirect and excluded from the idle window.
----------------------------------------------------------------------------
"""

import os
import re
import sys
import time
import logging
import threading
from datetime import datetime, timedelta
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# The FCLM title separator between process and function (rendered as a diamond
# glyph in the UI). We match it defensively — any of these count as a separator.
_TITLE_SEP = re.compile(r"[\u2666\u25c6\u2756>|/]+")

# Clock-frame titles: these define the paid window but are never "work".
_ONCLOCK_PAID_RE = re.compile(r"on\s*clock.*paid", re.IGNORECASE)
_OFFCLOCK_RE = re.compile(r"off\s*clock", re.IGNORECASE)

# Indirect / non-direct work titles. These are NOT idle and do NOT bridge a
# gap — the span they cover is removed from idle consideration.
_INDIRECT_KEYWORDS = (
    "train", "meeting", "indirect", "exempt", "leave", "break", "lunch",
    "vto", "vet", "problem solve", "water", "support", "admin", "orientation",
)

# Default threshold (minutes) for the single-continuous-gap flag.
DEFAULT_THRESHOLD_MIN = 30


# ============================================================
# Timestamp parsing
# ============================================================

def _parse_fclm_ts(raw, ref_year=None):
    """
    Parse an FCLM timeDetails timestamp like "10/10-6:32:00 PM" or
    "10/11-5:00:00 AM" into a datetime.

    FCLM omits the year, so we supply it from ref_year (defaults to current
    year). Returns None if the string can't be parsed.
    """
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    # Normalize the dash between date and time, and collapse whitespace.
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = re.sub(r"\s+", " ", s).strip()
    year = ref_year or datetime.now().year

    # Primary format (observed live): 24-hour "M/D-HH:MM:SS" with NO AM/PM,
    # e.g. "10/10-18:30:00" or "10/11-05:00:00".
    m = re.match(
        r"(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$",
        s,
    )
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        hour, minute = int(m.group(3)), int(m.group(4))
        second = int(m.group(5)) if m.group(5) else 0
        try:
            return datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None

    # Fallback: 12-hour "M/D-h:mm:ss AM/PM" (seconds optional).
    m = re.match(
        r"(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])",
        s,
    )
    if not m:
        return None
    month, day, hour, minute = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    second = int(m.group(5)) if m.group(5) else 0
    ampm = m.group(6).upper()
    if ampm == "PM" and hour != 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _duration_fallback_minutes(raw):
    """
    Parse the duration column as a fallback (only used for an open segment with
    a blank end). FCLM renders it as "MM:SS" or "H:MM"/"HH:MM"; we can't tell
    which without context, so we treat it as HH:MM when the first field is large
    (> 59 is impossible for minutes-of-MMSS, but we keep it simple): interpret
    "a:b" where if a <= 59 and we have no better info, assume MM:SS -> minutes.

    This is intentionally conservative and ONLY affects a currently-open
    segment; for all closed segments we compute from start/end instead.
    """
    if not raw:
        return 0.0
    s = raw.strip()
    m = re.match(r"(\d{1,3}):(\d{2})$", s)
    if not m:
        return 0.0
    a, b = int(m.group(1)), int(m.group(2))
    # Heuristic: durations in this report are typically MM:SS for short
    # segments. Return minutes (+ fractional seconds).
    return a + (b / 60.0)


# ============================================================
# Classification
# ============================================================

def classify_title(title):
    """
    Classify a timeDetails row title into one of:
        "onclock"  — OnClock/Paid frame
        "offclock" — OffClock/* (not paid)
        "direct"   — a direct process<SEP>function segment (e.g. Pick<SEP>...)
        "indirect" — training/meeting/exempt/leave/break/etc.
        "other"    — anything unrecognized (treated like indirect: excluded)
    """
    if not title:
        return "other"
    t = title.strip()
    low = t.lower()

    if _ONCLOCK_PAID_RE.search(low):
        return "onclock"
    if _OFFCLOCK_RE.search(low):
        return "offclock"

    for kw in _INDIRECT_KEYWORDS:
        if kw in low:
            return "indirect"

    # A direct segment looks like "Process<SEP>Function" (has the separator and
    # a recognizable process prefix). We accept any title with the separator
    # that is not a clock/indirect row.
    if _TITLE_SEP.search(t):
        return "direct"

    return "other"


def classify_segment(tr_classes, title):
    """
    Classify a gantt row using its <tr> CSS classes — the AUTHORITATIVE signal.

    FCLM labels every row by title (e.g. "Pick<SEP>Orderpicker Pick") regardless
    of whether the associate was actually producing. The real distinction is in
    the row's CSS class, which drives the colored bar:

        clock-seg on-clock            -> "onclock"  (paid frame)
        clock-seg off-clock           -> "offclock" (not paid)
        function-seg direct           -> "direct"   (actually working the path)
        job-seg direct / master       -> "direct"
        function-seg indirect         -> "idle"     (assigned to path, NOT working)
        function-seg indirect timeOffTask -> "idle" (Time Off Task)
        job-seg indirect / exempt / leav / master_indirect -> "indirect"
        function-seg edited           -> treat as "direct" (manually corrected work)
        transfer-seg                  -> "indirect" (labor transfer, not picking)

    Returns one of: "onclock","offclock","direct","idle","indirect","other".

    IMPORTANT: "idle" is the blue "in a direct path but not picking" bar — this
    is exactly the time we flag. It is distinct from "indirect" (meetings /
    training / exempt / leave), which is excluded entirely.
    """
    classes = set(c.lower() for c in (tr_classes or []))

    if "clock-seg" in classes:
        if "on-clock" in classes:
            return "onclock"
        if "off-clock" in classes:
            return "offclock"
        return "onclock" if "paid" in classes else "offclock"

    if "transfer-seg" in classes:
        return "indirect"

    if "function-seg" in classes:
        if "direct" in classes or "edited" in classes:
            return "direct"
        if "indirect" in classes or "timeofftask" in classes:
            # Assigned to a (direct) function but not producing -> idle.
            return "idle"
        return "other"

    if "job-seg" in classes:
        if "direct" in classes or "master" in classes:
            # "master" (not master_indirect) is a direct master job.
            if "master_indirect" in classes:
                return "indirect"
            return "direct"
        # exempt / leav / indirect / master_indirect
        return "indirect"

    # No recognizable segment class — fall back to the title-based classifier.
    return classify_title(title)


def direct_path_name(title):
    """
    Extract the process-path/function name from a direct segment title.

    FCLM direct titles look like "Pick<SEP>Orderpicker Pick" where <SEP> is a
    diamond glyph. We return the function portion after the separator (e.g.
    "Orderpicker Pick"), which matches the path names used elsewhere in the
    dashboard. Falls back to the whole title if there's no separator.
    """
    if not title:
        return "Unknown"
    parts = _TITLE_SEP.split(title.strip())
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return "Unknown"
    # The function name is the last meaningful segment (after Process<SEP>).
    return parts[-1]


# ============================================================
# Core idle computation
# ============================================================

def compute_idle(segments, now=None):
    """
    Given parsed segments, compute the single longest continuous idle gap.

    Args:
        segments: list of dicts with keys:
                  title (str), cls (str from classify_title),
                  start (datetime or None), end (datetime or None),
                  dur_fallback_min (float)
        now: datetime treated as "current time" for open segments
             (defaults to datetime.now()).

    Returns dict:
        {
          "longest_idle_min": float,     # single longest continuous gap
          "total_idle_min": float,       # sum of all gaps (informational)
          "direct_min": float,           # total direct time
          "paid_min": float,             # on-clock paid window length
          "window_start": datetime|None,
          "window_end": datetime|None,
        }

    Idle definition (updated for the live gantt): idle time is explicitly marked
    by FCLM as "indirect" FUNCTION segments under a direct path (the blue
    "assigned to the path but not producing" bars) — classified here as "idle".
    We also count uncovered gaps BETWEEN direct/idle activity. True "indirect"
    rows (meetings/training/exempt/leave/labor-transfer) are excised: they are
    not idle and they break a continuous idle run.

    The paid window is bounded by the associate's ACTUAL activity (first to last
    real segment), NOT the OnClock/Paid end — FCLM reports the OnClock end as the
    query boundary (e.g. 05:00), which would otherwise count all post-shift /
    on-leave time as idle.
    """
    now = now or datetime.now()

    # Real activity segments (anything that isn't a clock frame).
    activity = [s for s in segments
                if s["cls"] in ("direct", "idle", "indirect", "other") and s["start"]]
    if not activity:
        return _empty_idle()

    def _seg_end(s):
        end = s["end"]
        if end is None:
            return now if now > s["start"] else s["start"] + timedelta(
                minutes=s.get("dur_fallback_min", 0.0))
        return end

    # Window = span of actual activity (bounds the idle calc to on-task time).
    w_start = min(s["start"] for s in activity)
    w_end = max(_seg_end(s) for s in activity)

    # Intersect with the OnClock/Paid frame start if present (so pre-clock noise
    # is excluded), but DO NOT let the paid end extend the window past real work.
    paid_starts = [s["start"] for s in segments if s["cls"] == "onclock" and s["start"]]
    if paid_starts:
        w_start = max(w_start, min(paid_starts))

    if w_end <= w_start:
        return _empty_idle()

    paid_min = (w_end - w_start).total_seconds() / 60.0

    def _clamp(a, b):
        a2 = max(a, w_start)
        b2 = min(b, w_end)
        return (a2, b2) if b2 > a2 else None

    direct_intervals = []
    idle_intervals = []        # explicit blue "not working a direct path" bars
    indirect_intervals = []    # meetings/training/exempt/leave -> excised
    direct_min = 0.0
    # Tag both direct and idle intervals with their path for attribution.
    direct_tagged = []   # (start, end, path_name)
    idle_tagged = []     # (start, end, path_name)

    for s in segments:
        if not s["start"] or s["cls"] in ("onclock", "offclock"):
            continue
        iv = _clamp(s["start"], _seg_end(s))
        if not iv:
            continue
        path = direct_path_name(s.get("title", ""))
        if s["cls"] == "direct":
            direct_intervals.append(iv)
            direct_min += (iv[1] - iv[0]).total_seconds() / 60.0
            direct_tagged.append((iv[0], iv[1], path))
        elif s["cls"] == "idle":
            idle_intervals.append(iv)
            idle_tagged.append((iv[0], iv[1], path))
        else:  # indirect / other -> excised
            indirect_intervals.append(iv)

    merged_direct = _merge_intervals(direct_intervals)
    merged_indirect = _merge_intervals(indirect_intervals)

    # Uncovered gaps = window minus direct minus idle minus indirect. These are
    # periods with no segment at all; treat them as idle too (they occur between
    # direct/idle activity, not during meetings — indirect is subtracted).
    covered_all = _merge_intervals(merged_direct + _merge_intervals(idle_intervals) + merged_indirect)
    uncovered_gaps = _complement(w_start, w_end, covered_all)

    # Total idle = explicit idle bars + uncovered gaps, merged into continuous
    # runs. Indirect spans are NOT included and DO break runs (they're excised
    # from the number line, so a run can't span across them).
    idle_pieces = _merge_intervals(idle_intervals + uncovered_gaps)
    # Split any idle run that an indirect segment interrupts.
    idle_runs = _subtract_intervals(idle_pieces, merged_indirect)

    if not idle_runs:
        longest = 0.0
        total = 0.0
    else:
        run_mins = [(b - a).total_seconds() / 60.0 for (a, b) in idle_runs]
        longest = max(run_mins)
        total = sum(run_mins)

    # Per-path attribution of each idle run.
    by_path = _attribute_gaps_to_paths(idle_runs, direct_tagged + idle_tagged)

    # Per-gap breakdown (sorted longest -> shortest) so the UI can show each
    # idle gap with its start/end, duration, and the path it occurred on.
    tagged_all = sorted(direct_tagged + idle_tagged, key=lambda x: x[0])
    gaps_detail = []
    for (g_start, g_end) in idle_runs:
        g_min = (g_end - g_start).total_seconds() / 60.0
        if g_min <= 0:
            continue
        gaps_detail.append({
            "start": g_start.strftime("%H:%M"),
            "end": g_end.strftime("%H:%M"),
            "minutes": round(g_min, 1),
            "path": _gap_path(g_start, g_end, tagged_all),
        })
    gaps_detail.sort(key=lambda g: g["minutes"], reverse=True)

    return {
        "longest_idle_min": round(longest, 1),
        "total_idle_min": round(total, 1),
        "direct_min": round(direct_min, 1),
        "paid_min": round(paid_min, 1),
        "window_start": w_start,
        "window_end": w_end,
        "by_path": by_path,
        "gaps": gaps_detail,
    }


def _gap_path(g_start, g_end, tagged_sorted):
    """Path a gap is attributed to: the segment ending at/before the gap start,
    else the earliest segment starting at/after the gap end."""
    preceding = None
    for (d_start, d_end, d_path) in tagged_sorted:
        if d_end <= g_start:
            preceding = d_path
        elif d_start >= g_end:
            if preceding is None:
                return d_path
            break
    return preceding or "Unassigned"


def _attribute_gaps_to_paths(gaps, direct_tagged):
    """
    Attribute each idle gap to a direct path.

    Rule: a gap belongs to the direct segment ending at or before the gap's
    start (the path they went idle on). If none precedes it, use the earliest
    direct segment that starts at or after the gap's end.

    Args:
        gaps: list of (start, end) datetime tuples (uncovered idle intervals)
        direct_tagged: list of (start, end, path_name) for direct segments

    Returns:
        { path_name: {"idle_min": float, "longest_idle_min": float} }
    """
    out = {}
    if not gaps:
        return out
    tagged = sorted(direct_tagged, key=lambda x: x[0])

    for (g_start, g_end) in gaps:
        g_min = (g_end - g_start).total_seconds() / 60.0
        if g_min <= 0:
            continue

        # Preceding direct segment: latest one whose end <= gap start.
        preceding = None
        for (d_start, d_end, d_path) in tagged:
            if d_end <= g_start:
                preceding = d_path
            else:
                break

        path = preceding
        if path is None:
            # Gap before all direct work -> attribute to the following path.
            for (d_start, d_end, d_path) in tagged:
                if d_start >= g_end:
                    path = d_path
                    break
        if path is None:
            path = "Unassigned"

        bucket = out.setdefault(path, {"idle_min": 0.0, "longest_idle_min": 0.0})
        bucket["idle_min"] += g_min
        if g_min > bucket["longest_idle_min"]:
            bucket["longest_idle_min"] = g_min

    # Round for presentation.
    for p in out:
        out[p]["idle_min"] = round(out[p]["idle_min"], 1)
        out[p]["longest_idle_min"] = round(out[p]["longest_idle_min"], 1)
    return out


def _empty_idle():
    return {
        "longest_idle_min": 0.0,
        "total_idle_min": 0.0,
        "direct_min": 0.0,
        "paid_min": 0.0,
        "window_start": None,
        "window_end": None,
        "by_path": {},
        "gaps": [],
    }


def _merge_intervals(intervals):
    """Merge overlapping/adjacent (datetime, datetime) intervals."""
    if not intervals:
        return []
    ivs = sorted(intervals, key=lambda x: x[0])
    merged = [list(ivs[0])]
    for a, b in ivs[1:]:
        if a <= merged[-1][1]:
            if b > merged[-1][1]:
                merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _complement(w_start, w_end, covered):
    """Return the gaps in [w_start, w_end] not covered by `covered` intervals."""
    gaps = []
    cursor = w_start
    for a, b in covered:
        if a > cursor:
            gaps.append((cursor, a))
        if b > cursor:
            cursor = b
    if cursor < w_end:
        gaps.append((cursor, w_end))
    return gaps


def _subtract_intervals(intervals, cut):
    """
    Subtract `cut` intervals from each interval in `intervals`, splitting them
    where a cut overlaps. Returns the surviving sub-intervals.

    Used to break an idle run wherever an indirect (meeting/training) segment
    falls inside it, so a continuous idle gap never spans across indirect time.
    """
    if not cut:
        return list(intervals)
    cut_m = _merge_intervals(cut)
    out = []
    for (a, b) in intervals:
        cursor = a
        for (ca, cb) in cut_m:
            if cb <= cursor or ca >= b:
                continue
            if ca > cursor:
                out.append((cursor, min(ca, b)))
            cursor = max(cursor, cb)
            if cursor >= b:
                break
        if cursor < b:
            out.append((cursor, b))
    return [(a, b) for (a, b) in out if b > a]


# ============================================================
# HTML parsing
# ============================================================

def parse_time_details(html_content, now=None):
    """
    Parse an FCLM Employee Time Details page into:
        { "login": str|None, "idle": <compute_idle result> }

    Returns login (lowercased) if found in the Employee Info block, plus the
    idle computation. If the time table can't be found, idle is the empty
    result and login may still be resolved from the header.
    """
    from bs4 import BeautifulSoup

    now = now or datetime.now()
    if not html_content:
        return {"login": None, "idle": _empty_idle()}

    soup = BeautifulSoup(html_content, "html.parser")

    login = _extract_login(soup)
    ref_year = now.year

    # Find the time-details table. Live FCLM renders it as <table class="ganttChart">
    # with a header row (title | start | end | duration) a few rows down (after a
    # date-range row and an "Hours on Task" row). Prefer that table explicitly,
    # then fall back to any table containing the title/start/duration header.
    target = soup.find("table", class_="ganttChart")
    if target is None:
        for table in soup.find_all("table"):
            # Scan the whole table (not just the first few cells) for the header.
            cells = [c.get_text(strip=True).lower()
                     for c in table.find_all(["th", "td"], limit=40)]
            joined = " ".join(cells)
            if "title" in joined and "start" in joined and "duration" in joined:
                target = table
                break

    if target is None:
        return {"login": login, "idle": _empty_idle()}

    segments = []
    rows = target.find_all("tr")
    for row in rows:
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 4:
            continue
        title = cells[0]
        if title.lower() == "title":
            continue  # header row
        start_raw, end_raw, dur_raw = cells[1], cells[2], cells[3]
        start = _parse_fclm_ts(start_raw, ref_year)
        end = _parse_fclm_ts(end_raw, ref_year)
        # Handle overnight rollover: if end < start, it crossed midnight.
        if start and end and end < start:
            end = end + timedelta(days=1)
        if not start:
            continue
        # Classify using the <tr> CSS class (authoritative), falling back to the
        # title. This is what distinguishes a blue "assigned but not picking"
        # (idle) bar from a real "direct" working bar — both share the same
        # Pick<SEP>Orderpicker Pick title.
        tr_classes = row.get("class", [])
        if isinstance(tr_classes, str):
            tr_classes = tr_classes.split()
        cls = classify_segment(tr_classes, title)
        segments.append({
            "title": title,
            "cls": cls,
            "start": start,
            "end": end,
            "dur_fallback_min": _duration_fallback_minutes(dur_raw),
        })

    idle = compute_idle(segments, now=now)
    return {"login": login, "idle": idle}


def _extract_login(soup):
    """Extract the login/alias from the Employee Info header block."""
    # Strategy 1: a "Login" label cell followed by the value cell.
    for cell in soup.find_all(["td", "th", "div", "span", "b", "strong"]):
        txt = cell.get_text(strip=True)
        if txt.lower() == "login":
            # Value is usually the next sibling cell.
            nxt = cell.find_next(["td", "div", "span"])
            if nxt:
                v = nxt.get_text(strip=True)
                if _looks_like_login(v):
                    return v.lower()
    # Strategy 2: header title like "Haywood,LaNece (fayehayw)(54m)"
    text = soup.get_text(" ", strip=True)
    m = re.search(r"\(([a-z][a-z0-9._-]{2,20})\)", text)
    if m:
        return m.group(1).lower()
    return None


_LOGIN_RE = re.compile(r"^[a-z][a-z0-9._-]{2,20}$", re.IGNORECASE)


def _looks_like_login(v):
    return bool(v) and not v.isdigit() and bool(_LOGIN_RE.match(v))


# ============================================================
# Fetch
# ============================================================

def build_time_details_url(warehouse_id, employee_id, start_dt, end_dt):
    """
    Build the FCLM Employee Time Details (timeDetails) URL for one associate.

    The real endpoint takes the window as ISO-8601 UTC timestamps:
        ?employeeId=<id>&warehouseId=<FC>
        &startTime=2026-10-10T23:30:00.000Z
        &endTime=2026-10-11T10:00:00.000Z

    start_dt / end_dt are naive LOCAL datetimes (the dashboard's shift window).
    We convert them to UTC using the local machine's timezone — the machine
    running PickMatrix is on the FC's local clock, and FCLM expects UTC here
    (e.g. 6:30 PM Central -> 23:30Z).
    """
    base = "https://fclm-portal.amazon.com/employee/timeDetails"
    start_z = _to_utc_z(start_dt)
    end_z = _to_utc_z(end_dt)
    params = urlencode({
        "employeeId": employee_id,
        "warehouseId": warehouse_id,
        "startTime": start_z,
        "endTime": end_z,
    })
    return f"{base}?{params}"


def _to_utc_z(dt):
    """
    Convert a naive LOCAL datetime to an FCLM UTC ISO-8601 string with
    millisecond precision and a trailing Z, e.g. '2026-10-10T23:30:00.000Z'.
    """
    from datetime import timezone
    if dt.tzinfo is None:
        # Interpret as local time, then convert to UTC.
        local_aware = dt.astimezone()  # attaches local tz
    else:
        local_aware = dt
    utc = local_aware.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(utc.microsecond/1000):03d}Z"


def _fclm_session(config):
    """Build a requests session authenticated with Midway cookies."""
    import requests
    import urllib3
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fclm_rate_puller import get_midway_cookies

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    cookies = get_midway_cookies(config)
    if not cookies:
        return None

    session = requests.Session()
    session.verify = False
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) "
                      "Gecko/20100101 Firefox/120.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    for name, value in cookies.items():
        session.cookies.set(name, value, domain="fclm-portal.amazon.com")
        session.cookies.set(name, value, domain="midway-auth.amazon.com")
    return session


def fetch_idle_for_employee(employee_id, config, start_dt, end_dt, session=None, now=None):
    """
    Fetch + parse one associate's idle time.

    Returns the parse_time_details dict, with employee_id added, or None on
    fetch failure.
    """
    own_session = False
    if session is None:
        session = _fclm_session(config)
        own_session = True
    if session is None:
        logger.warning("Idle: no cookies available")
        return None

    warehouse_id = config.get("warehouse_id", "HOU8")
    url = build_time_details_url(warehouse_id, employee_id, start_dt, end_dt)
    try:
        r = session.get(url, timeout=45)
    except Exception as e:
        logger.warning("Idle fetch failed for %s: %s", employee_id, e)
        return None
    finally:
        if own_session:
            try:
                session.close()
            except Exception:
                pass

    if r.status_code != 200:
        logger.warning("Idle: timeDetails HTTP %s for %s", r.status_code, employee_id)
        return None

    result = parse_time_details(r.text, now=now)
    result["employee_id"] = str(employee_id)
    return result


def scan_idle(employees, config, start_dt, end_dt, threshold_min=DEFAULT_THRESHOLD_MIN,
              now=None, max_workers=8, on_result=None):
    """
    Scan a set of associates for long idle gaps — CONCURRENTLY.

    Each associate requires one FCLM timeDetails request; doing them serially is
    slow for a full shift (60-280 pickers). We fan the requests out across a
    thread pool and publish each result as it completes so the UI can fill in
    incrementally instead of waiting for the whole batch.

    Args:
        employees: iterable of (employee_id, login_hint) tuples OR bare
                   employee_id strings. login_hint is optional; if the page
                   has a login it wins.
        config: FCLM config dict (warehouse_id, cookie settings).
        start_dt, end_dt: shift window (datetimes).
        threshold_min: flag threshold for the single longest gap.
        now: current-time override (for open segments / tests).
        max_workers: concurrent FCLM requests (bounded to be polite to FCLM).
        on_result: optional callback invoked as on_result(row) each time one
                   associate finishes. row = {login, idle_min, flagged, by_path}.
                   Lets the server stream partial results to the dashboard.

    Returns:
        list of dicts: { "login", "idle_min", "flagged", "by_path" } sorted by
        idle_min desc.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    now = now or datetime.now()

    # Build one authenticated session and share it across worker threads.
    # requests.Session is safe for concurrent GETs to distinct URLs; a shared
    # connection pool also avoids re-reading the Firefox cookie DB per request.
    session = _fclm_session(config)
    if session is None:
        logger.warning("Idle scan: no cookies available")
        return []
    # Allow enough pooled connections for the worker count.
    try:
        import requests.adapters as _ra
        adapter = _ra.HTTPAdapter(pool_connections=max_workers, pool_maxsize=max_workers)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    except Exception:
        pass

    # Normalize the employee list.
    tasks = []
    for item in employees:
        if isinstance(item, (tuple, list)):
            eid, login_hint = item[0], (item[1] if len(item) > 1 else None)
        else:
            eid, login_hint = item, None
        eid = str(eid).strip()
        if eid:
            tasks.append((eid, login_hint))

    if not tasks:
        try:
            session.close()
        except Exception:
            pass
        return []

    workers = max(1, min(max_workers, len(tasks)))
    warehouse_id = config.get("warehouse_id", "HOU8")

    def _one(eid, login_hint):
        parsed = fetch_idle_for_employee(
            eid, config, start_dt, end_dt, session=session, now=now
        )
        if parsed is None:
            return None
        login = parsed.get("login") or (login_hint.lower() if login_hint else None) or eid
        idle = parsed["idle"]
        longest = idle["longest_idle_min"]
        total_idle = idle.get("total_idle_min", 0.0)
        direct = idle.get("direct_min", 0.0)
        shift_min = idle.get("paid_min", 0.0)   # total time on shift (on-clock)
        # Idle % is measured against TOTAL TIME ON SHIFT (not just active time),
        # so it reflects the share of the whole shift spent idle.
        idle_pct = round((total_idle / shift_min) * 100, 1) if shift_min > 0 else 0.0
        prod_pct = round((direct / shift_min) * 100, 1) if shift_min > 0 else 0.0
        # Direct FCLM Time Details link for the exact window — always buildable
        # here because eid is the employee_id we queried.
        try:
            fclm_url = build_time_details_url(warehouse_id, eid, start_dt, end_dt)
        except Exception:
            fclm_url = ""
        return {
            "login": login,
            "employee_id": str(eid),
            "idle_min": longest,            # single longest continuous gap (flag basis)
            "total_idle_min": round(total_idle, 1),
            "direct_min": round(direct, 1),
            "shift_min": round(shift_min, 1),
            "idle_pct": idle_pct,
            "prod_pct": prod_pct,
            "flagged": longest >= threshold_min,
            "by_path": idle.get("by_path", {}),
            "gaps": idle.get("gaps", []),
            "fclm_url": fclm_url,
        }

    results = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_one, eid, hint): eid for (eid, hint) in tasks}
            for fut in as_completed(futures):
                try:
                    row = fut.result()
                except Exception as e:
                    logger.warning("Idle worker error for %s: %s", futures[fut], e)
                    row = None
                if row is None:
                    continue
                results.append(row)
                if on_result is not None:
                    try:
                        on_result(row)
                    except Exception as e:
                        logger.warning("Idle on_result callback error: %s", e)
    finally:
        try:
            session.close()
        except Exception:
            pass

    results.sort(key=lambda r: r["idle_min"], reverse=True)
    logger.info(
        "Idle scan: %d associates checked, %d flagged (>= %d min)",
        len(results), sum(1 for r in results if r["flagged"]), threshold_min,
    )
    return results


if __name__ == "__main__":
    # Quick manual test against a saved HTML file:
    #   python idle_time.py path\to\saved_timedetails.html
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        with open(sys.argv[1], "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
        out = parse_time_details(html)
        print("login:", out["login"])
        idle = out["idle"]
        print(f"longest continuous idle: {idle['longest_idle_min']} min")
        print(f"total idle: {idle['total_idle_min']} min")
        print(f"direct: {idle['direct_min']} min | paid window: {idle['paid_min']} min")
        print(f"FLAGGED (>= {DEFAULT_THRESHOLD_MIN} min): "
              f"{idle['longest_idle_min'] >= DEFAULT_THRESHOLD_MIN}")
    else:
        print("Usage: python idle_time.py <saved_timedetails.html>")
