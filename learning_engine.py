"""
Learning Engine - Improves staffing recommendations over time.

Tracks:
1. Move decisions made by users (who was moved, from where, to where)
2. Outcomes after the move (did the destination path hit rate? did the associate perform?)
3. Pattern recognition (which types of moves tend to succeed/fail)

The system learns:
- Which associates perform well when moved to specific paths
- What rate delta to expect when moving someone (actual vs predicted)
- Time-of-shift patterns (some associates ramp up/down at different hours)
- Success rates per path pair (e.g., "RF Pick Singles -> OrderPickVNA" moves succeed 73% of the time)
"""

import sqlite3
import os
import logging
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)

def _site_db_path():
    # Must match rate_history._site_db_path so both share one per-site DB file.
    _dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'PickMatrix')
    site = (os.environ.get('PICKMATRIX_SITE') or '').strip().upper()
    if site:
        return os.path.join(_dir, f"rate_history_{site}.db")
    return os.path.join(_dir, "rate_history.db")


DB_PATH = _site_db_path()


# Map the FCLM path names used for move detection to the Picking Console
# process-path names operators actually see (PPSingle*, PPMulti*, etc.).
# FCLM collapses several console sub-paths into one bucket, so for those buckets
# we show BOTH console names (e.g. "PPSingleOPBOD / PPSingleOPNonCon") since FCLM
# alone cannot tell which sub-path the move was on.
FCLM_TO_CONSOLE_PATH = {
    "RF Pick Singles": "PPSingleFloor",
    "OrderPickVNA": "PPSingleOPVNA",
    "Orderpicker Pick": "PPSingleOP",
    "OrderPickLowDensityP": "BOD/NonCon",
    "OrderPick SIOC": "PPSingleSSD",
    "MultiRelayPick": "PPMulti-BldgWide/SSD",
    "Giftwrap Picking": "PPSingleGiftwrap",
    "RF Pick": "PPSingleRFPick",
    "Pallet Pick": "PPPalletPick",
    "Teamlift Pick": "PPTeamlift",
}


def to_console_path(name):
    """Return the Picking Console process-path name for an FCLM path name.

    If the name is already a console-style name (starts with 'PP') or is
    unknown, it is returned unchanged so nothing is ever lost.
    """
    if not name:
        return name
    if name in FCLM_TO_CONSOLE_PATH:
        return FCLM_TO_CONSOLE_PATH[name]
    return name


def _get_db():
    """Get DB connection with learning tables."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    
    # Move decisions log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS move_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            employee_id TEXT NOT NULL,
            login TEXT,
            name TEXT,
            from_path TEXT NOT NULL,
            to_path TEXT NOT NULL,
            from_rate REAL,
            goal_uph REAL,
            predicted_verdict TEXT,
            predicted_confidence TEXT
        )
    """)
    
    # Move outcomes (recorded after the move)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS move_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            move_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            actual_rate REAL,
            goal_uph REAL,
            hit_goal INTEGER,
            hours_on_new_path REAL,
            FOREIGN KEY (move_id) REFERENCES move_log(id)
        )
    """)
    
    # Path pair success rates (aggregated learning)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS path_pair_stats (
            from_path TEXT NOT NULL,
            to_path TEXT NOT NULL,
            total_moves INTEGER DEFAULT 0,
            successful_moves INTEGER DEFAULT 0,
            avg_rate_achieved REAL DEFAULT 0,
            avg_rate_vs_goal REAL DEFAULT 0,
            last_updated TEXT,
            PRIMARY KEY (from_path, to_path)
        )
    """)
    
    # Associate adaptability scores
    conn.execute("""
        CREATE TABLE IF NOT EXISTS associate_scores (
            employee_id TEXT PRIMARY KEY,
            total_moves INTEGER DEFAULT 0,
            successful_moves INTEGER DEFAULT 0,
            adaptability_score REAL DEFAULT 0.5,
            avg_ramp_time_hours REAL DEFAULT 0,
            paths_mastered TEXT DEFAULT '',
            last_updated TEXT
        )
    """)

    # Authoritative employee_id -> login map, bridged from the Picking Console
    # workforce feed (login) joined to FCLM (employee_id) on AA name.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS employee_login_map (
            employee_id TEXT PRIMARY KEY,
            login TEXT NOT NULL,
            last_updated TEXT NOT NULL
        )
    """)

    conn.commit()
    return conn


def upsert_employee_logins(mapping):
    """
    Persist employee_id -> login pairs (from the Picking Console bridge).

    Also backfills the login onto any existing move_log rows for that
    employee_id that are missing it, so the move log immediately shows the
    login instead of just the employee id.

    Args:
        mapping: dict {employee_id: login}

    Returns:
        Number of employee_ids written.
    """
    if not mapping:
        return 0

    conn = _get_db()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for eid, login in mapping.items():
        if not eid or not login:
            continue
        cursor.execute("""
            INSERT INTO employee_login_map (employee_id, login, last_updated)
            VALUES (?, ?, ?)
            ON CONFLICT(employee_id) DO UPDATE SET login=excluded.login, last_updated=excluded.last_updated
        """, (eid, login, now))
        # Backfill move_log rows that never got a login
        cursor.execute("""
            UPDATE move_log SET login = ?
            WHERE employee_id = ? AND (login IS NULL OR login = '')
        """, (login, eid))

    conn.commit()
    conn.close()
    return len(mapping)


def get_employee_login_map():
    """Return the persisted employee_id -> login map."""
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT employee_id, login FROM employee_login_map")
    rows = cursor.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def log_move_decision(employee_id, login, name, from_path, to_path, from_rate, goal_uph, predicted_verdict, predicted_confidence):
    """
    Record that a user decided to move an associate.
    Called when a user acts on a recommendation.
    
    Returns the move_id for tracking the outcome later.
    """
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO move_log (timestamp, employee_id, login, name, from_path, to_path, from_rate, goal_uph, predicted_verdict, predicted_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        employee_id, login, name, from_path, to_path, from_rate, goal_uph,
        predicted_verdict, predicted_confidence
    ))
    move_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    logger.info("Move logged: %s from %s -> %s (predicted: %s)", login or employee_id, from_path, to_path, predicted_verdict)
    return move_id


def record_move_outcome(move_id, actual_rate, goal_uph, hours_on_new_path):
    """
    Record the outcome of a move (called automatically when we see the associate
    producing on their new path in subsequent data refreshes).
    """
    hit_goal = 1 if actual_rate >= goal_uph else 0
    
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO move_outcomes (move_id, timestamp, actual_rate, goal_uph, hit_goal, hours_on_new_path)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (move_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), actual_rate, goal_uph, hit_goal, hours_on_new_path))
    conn.commit()
    
    # Update path pair stats
    cursor.execute("SELECT from_path, to_path FROM move_log WHERE id = ?", (move_id,))
    row = cursor.fetchone()
    if row:
        _update_path_pair_stats(conn, row[0], row[1])
    
    # Update associate score
    cursor.execute("SELECT employee_id FROM move_log WHERE id = ?", (move_id,))
    row = cursor.fetchone()
    if row:
        _update_associate_score(conn, row[0])
    
    conn.close()


def auto_track_outcomes(associates_by_path):
    """
    Automatically detect and record outcomes for recent moves.
    
    Called on each data refresh. Checks if any recently-moved associates
    are now producing on their new path and records the outcome.
    """
    if not associates_by_path:
        return
    
    conn = _get_db()
    cursor = conn.cursor()
    
    # Find moves from the last 4 hours that don't have outcomes yet
    cutoff = (datetime.now() - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        SELECT ml.id, ml.employee_id, ml.to_path, ml.goal_uph
        FROM move_log ml
        LEFT JOIN move_outcomes mo ON mo.move_id = ml.id
        WHERE ml.timestamp >= ? AND mo.id IS NULL
    """, (cutoff,))
    
    pending_moves = cursor.fetchall()
    
    for move_id, employee_id, to_path, goal_uph in pending_moves:
        # Check if this associate is now working on the destination path
        dest_assocs = associates_by_path.get(to_path, [])
        for a in dest_assocs:
            if a.get("employee_id") == employee_id:
                rate = a.get("rate", 0) or 0
                hours = a.get("paid_hours", 0) or 0
                if rate > 0 and hours > 0:
                    record_move_outcome(move_id, rate, goal_uph, hours)
                    logger.info("  Outcome recorded: %s on %s = %.1f UPH (goal: %.0f)", 
                              employee_id, to_path, rate, goal_uph)
                break
    
    conn.close()


def auto_detect_moves(associates_by_path, rate_expectations, default_goal=5):
    """
    Automatically detect when associates have moved to a different path
    WITHOUT the user manually logging it.
    
    Compares current path assignments vs the last known path for each associate.
    If someone is on a different path than before, logs it as an auto-detected move.
    
    Called on each data refresh.
    """
    if not associates_by_path:
        return
    
    conn = _get_db()
    cursor = conn.cursor()
    
    # Ensure we have a table to track last-known paths
    conn.execute("""
        CREATE TABLE IF NOT EXISTS last_known_path (
            employee_id TEXT PRIMARY KEY,
            process_path TEXT NOT NULL,
            login TEXT,
            name TEXT,
            rate REAL,
            last_seen TEXT NOT NULL
        )
    """)
    conn.commit()
    
    # Build current snapshot: employee_id -> (path, login, name, rate)
    current_snapshot = {}
    for path_name, associates in associates_by_path.items():
        for a in associates:
            eid = a.get("employee_id", "")
            if eid and (a.get("paid_hours", 0) or 0) > 0:
                current_snapshot[eid] = {
                    "path": path_name,
                    "login": a.get("login", ""),
                    "name": a.get("name", ""),
                    "rate": a.get("rate", 0) or 0,
                }
    
    # Compare with last known paths
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    moves_detected = 0
    
    for eid, current in current_snapshot.items():
        cursor.execute("SELECT process_path, rate FROM last_known_path WHERE employee_id = ?", (eid,))
        row = cursor.fetchone()
        
        if row:
            prev_path = row[0]
            prev_rate = row[1] or 0
            
            if prev_path != current["path"]:
                # This associate moved! Check if already logged recently
                recent_cutoff = (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("""
                    SELECT id FROM move_log 
                    WHERE employee_id = ? AND to_path = ? AND timestamp >= ?
                """, (eid, current["path"], recent_cutoff))
                
                already_logged = cursor.fetchone()
                
                if not already_logged:
                    # Auto-log this move
                    goal = rate_expectations.get(current["path"], default_goal)
                    cursor.execute("""
                        INSERT INTO move_log (timestamp, employee_id, login, name, from_path, to_path, from_rate, goal_uph, predicted_verdict, predicted_confidence)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        now_str, eid, current["login"], current["name"],
                        prev_path, current["path"], prev_rate, goal,
                        "AUTO-DETECTED", "auto"
                    ))
                    moves_detected += 1
                    logger.info("  Auto-detected move: %s (%s) from %s -> %s",
                              current["login"] or eid, current["name"], prev_path, current["path"])
        
        # Update last known path
        cursor.execute("""
            INSERT OR REPLACE INTO last_known_path (employee_id, process_path, login, name, rate, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (eid, current["path"], current["login"], current["name"], current["rate"], now_str))
    
    if moves_detected > 0:
        logger.info("Auto-detected %d moves this refresh", moves_detected)
    
    conn.commit()
    conn.close()


def _update_path_pair_stats(conn, from_path, to_path):
    """Recalculate aggregate stats for a path pair."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*), SUM(mo.hit_goal), AVG(mo.actual_rate), AVG(mo.actual_rate - mo.goal_uph)
        FROM move_outcomes mo
        JOIN move_log ml ON ml.id = mo.move_id
        WHERE ml.from_path = ? AND ml.to_path = ?
    """, (from_path, to_path))
    
    row = cursor.fetchone()
    if row and row[0] > 0:
        cursor.execute("""
            INSERT OR REPLACE INTO path_pair_stats (from_path, to_path, total_moves, successful_moves, avg_rate_achieved, avg_rate_vs_goal, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (from_path, to_path, row[0], row[1] or 0, row[2] or 0, row[3] or 0, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()


def _update_associate_score(conn, employee_id):
    """Recalculate an associate's adaptability score."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*), SUM(mo.hit_goal)
        FROM move_outcomes mo
        JOIN move_log ml ON ml.id = mo.move_id
        WHERE ml.employee_id = ?
    """, (employee_id,))
    
    row = cursor.fetchone()
    if row and row[0] > 0:
        total = row[0]
        successes = row[1] or 0
        score = successes / total  # 0.0 to 1.0
        
        # Get distinct paths they've successfully worked on
        cursor.execute("""
            SELECT GROUP_CONCAT(DISTINCT ml.to_path)
            FROM move_outcomes mo
            JOIN move_log ml ON ml.id = mo.move_id
            WHERE ml.employee_id = ? AND mo.hit_goal = 1
        """, (employee_id,))
        paths_row = cursor.fetchone()
        paths_mastered = paths_row[0] if paths_row and paths_row[0] else ""
        
        cursor.execute("""
            INSERT OR REPLACE INTO associate_scores (employee_id, total_moves, successful_moves, adaptability_score, paths_mastered, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (employee_id, total, successes, round(score, 2), paths_mastered, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()


def get_path_pair_insight(from_path, to_path):
    """
    Get learned insights for a specific path pair.
    
    Returns what the system has learned about moves from one path to another.
    """
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT total_moves, successful_moves, avg_rate_achieved, avg_rate_vs_goal
        FROM path_pair_stats
        WHERE from_path = ? AND to_path = ?
    """, (from_path, to_path))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row or row[0] == 0:
        return None
    
    return {
        "total_moves": row[0],
        "successful_moves": row[1],
        "success_rate": round((row[1] / row[0]) * 100, 1) if row[0] > 0 else 0,
        "avg_rate_achieved": round(row[2], 1),
        "avg_rate_vs_goal": round(row[3], 1),
    }


def get_associate_adaptability(employee_id):
    """Get an associate's learned adaptability score."""
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT total_moves, successful_moves, adaptability_score, paths_mastered
        FROM associate_scores
        WHERE employee_id = ?
    """, (employee_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
    
    return {
        "total_moves": row[0],
        "successful_moves": row[1],
        "adaptability_score": row[2],
        "paths_mastered": row[3].split(",") if row[3] else [],
    }


def enhance_recommendation(recommendation, from_path, to_path, employee_id):
    """
    Enhance a recommendation with learned data.
    
    Adds:
    - Path pair success rate (if we have data)
    - Associate's adaptability score
    - Confidence adjustment based on learning
    """
    # Path pair insight
    pair_insight = get_path_pair_insight(from_path, to_path)
    if pair_insight and pair_insight["total_moves"] >= 3:
        recommendation["path_pair_insight"] = pair_insight
        recommendation["reasoning"].append(
            f"Historical: {from_path} -> {to_path} moves succeed {pair_insight['success_rate']:.0f}% of the time "
            f"({pair_insight['successful_moves']}/{pair_insight['total_moves']} moves)"
        )
        
        # Adjust confidence based on path pair data
        if pair_insight["success_rate"] >= 70:
            if recommendation["verdict"] in ("GOOD MOVE", "OKAY MOVE"):
                recommendation["confidence"] = "high"
        elif pair_insight["success_rate"] < 40:
            if recommendation["verdict"] != "RISKY MOVE":
                recommendation["reasoning"].append(
                    f"WARNING: This path pair has a low historical success rate"
                )
    
    # Associate adaptability
    adapt = get_associate_adaptability(employee_id)
    if adapt and adapt["total_moves"] >= 2:
        recommendation["adaptability"] = adapt
        if adapt["adaptability_score"] >= 0.7:
            recommendation["reasoning"].append(
                f"This associate adapts well to moves ({adapt['adaptability_score']:.0f}% success rate across {adapt['total_moves']} moves)"
            )
        elif adapt["adaptability_score"] < 0.4:
            recommendation["reasoning"].append(
                f"This associate has struggled with past moves ({adapt['adaptability_score']:.0f}% success rate)"
            )
    
    return recommendation


def get_learning_summary():
    """Get overall learning statistics for the dashboard."""
    conn = _get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM move_log")
    total_moves = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM move_outcomes WHERE hit_goal = 1")
    successful = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM move_outcomes")
    with_outcomes = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM associate_scores WHERE total_moves >= 2")
    tracked_associates = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM path_pair_stats WHERE total_moves >= 3")
    learned_pairs = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        "total_moves_logged": total_moves,
        "moves_with_outcomes": with_outcomes,
        "successful_outcomes": successful,
        "overall_success_rate": round((successful / with_outcomes) * 100, 1) if with_outcomes > 0 else 0,
        "tracked_associates": tracked_associates,
        "learned_path_pairs": learned_pairs,
    }


# Console process paths that are NOT part of the pickable/fungible move log.
# Moves into or out of these are ignored (problem-solve, transship, counts...).
_CONSOLE_EXCLUDE_PATHS = {
    'PPTrans', 'PPTransOut', 'PPTransIn', 'PPQA', 'PPICQA', 'PPCount',
    'PPRebinHotpick', 'PPRebin',
}


def detect_console_moves(pickers, rate_expectations=None, default_goal=5):
    """
    Detect and log path moves directly from the Picking Console workforce feed.

    This is the authoritative move source. Each picker in the feed carries:
        userId      -> the AA login
        processPath -> the exact console path (PPSingleOP, PPSingleOPNonCon, ...)
        name        -> AA name (used only to enrich the row, never required)

    On each push we compare a picker's current processPath to the last one we
    saw for that login. If it changed, we log a move with the console path names
    stored directly (no FCLM, no employee_id, no name mapping needed).

    Args:
        pickers: list of picker dicts from the console feed (pickerStatusList).
        rate_expectations: optional {path: goal_uph} for context (best-effort).
        default_goal: fallback goal.

    Returns:
        Number of moves detected this call.
    """
    if not pickers:
        return 0

    rate_expectations = rate_expectations or {}

    conn = _get_db()
    cursor = conn.cursor()

    # Track last-known console path per login
    conn.execute("""
        CREATE TABLE IF NOT EXISTS last_known_console_path (
            login TEXT PRIMARY KEY,
            process_path TEXT NOT NULL,
            name TEXT,
            last_seen TEXT NOT NULL
        )
    """)
    conn.commit()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    moves_detected = 0

    # Build current snapshot: login -> (processPath, name)
    current = {}
    for p in pickers:
        login = p.get("userId") or p.get("login", "")
        pp = p.get("processPath", "")
        if not login or not pp:
            continue
        # Skip non-pickable paths
        if pp in _CONSOLE_EXCLUDE_PATHS or pp.startswith('PPTrans') or pp.startswith('PPQA'):
            continue
        current[login] = (pp, p.get("name", ""))

    for login, (pp, name) in current.items():
        cursor.execute("SELECT process_path FROM last_known_console_path WHERE login = ?", (login,))
        row = cursor.fetchone()

        if row and row[0] != pp:
            prev_path = row[0]
            # Avoid duplicate logging if we already logged this exact move recently
            recent_cutoff = (datetime.now() - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
                SELECT id FROM move_log
                WHERE login = ? AND from_path = ? AND to_path = ? AND timestamp >= ?
            """, (login, prev_path, pp, recent_cutoff))
            if not cursor.fetchone():
                goal = rate_expectations.get(pp, default_goal)
                cursor.execute("""
                    INSERT INTO move_log (timestamp, employee_id, login, name, from_path, to_path, from_rate, goal_uph, predicted_verdict, predicted_confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now_str, "", login, name, prev_path, pp, 0, goal,
                    "AUTO-DETECTED", "auto"
                ))
                moves_detected += 1
                logger.info("  Console move: %s from %s -> %s", login, prev_path, pp)

        cursor.execute("""
            INSERT OR REPLACE INTO last_known_console_path (login, process_path, name, last_seen)
            VALUES (?, ?, ?, ?)
        """, (login, pp, name, now_str))

    if moves_detected > 0:
        logger.info("Detected %d console moves this push", moves_detected)

    conn.commit()
    conn.close()
    return moves_detected


def get_recent_moves(login=None, employee_id=None, hours=None, limit=200, start=None, end=None):
    """
    Read the path-move data log for the dashboard.

    Returns each detected/logged move of a picker from one process path to
    another, newest first. Every row records who moved (login/name/employee_id),
    the path they moved FROM, the path they moved TO, and WHEN it happened.

    Args:
        login: Optional case-insensitive login filter (exact match).
        employee_id: Optional employee_id filter (exact match).
        hours: Optional look-back window in hours (e.g. 12 = only moves in the
               last 12 hours). None returns everything within `limit`.
        limit: Max number of rows to return.
        start: Optional lower bound timestamp "YYYY-MM-DD HH:MM:SS"
               (or ISO "YYYY-MM-DDTHH:MM"). Moves at/after this time.
        end:   Optional upper bound timestamp. Moves at/before this time.
               start/end let the UI show only the moves within a shift window.

    Returns:
        list[dict]: [{timestamp, employee_id, login, name, from_path, to_path,
                      from_rate, source}, ...]
    """
    def _norm_ts(v):
        if not v:
            return None
        v = v.replace("T", " ").strip()
        # Accept "YYYY-MM-DD HH:MM" and pad seconds
        if len(v) == 16:
            v += ":00"
        return v

    start = _norm_ts(start)
    end = _norm_ts(end)

    conn = _get_db()
    cursor = conn.cursor()

    where = []
    params = []

    # Only show moves detected from the Picking Console. Legacy FCLM-detected
    # rows use FCLM bucket names (e.g. "OrderPickLowDensityP", "Orderpicker
    # Pick"); we exclude any row whose from/to path is one of those names so the
    # log reflects console process paths only.
    fclm_names = list(FCLM_TO_CONSOLE_PATH.keys())
    if fclm_names:
        qm = ",".join("?" for _ in fclm_names)
        where.append(f"from_path NOT IN ({qm})")
        params.extend(fclm_names)
        where.append(f"to_path NOT IN ({qm})")
        params.extend(fclm_names)

    # Note: the login filter is applied in Python AFTER we recover any missing
    # logins from rate_history, so a move row with a blank stored login can
    # still be matched by the login we recover for it.
    if employee_id:
        where.append("employee_id = ?")
        params.append(employee_id)
    if start:
        where.append("timestamp >= ?")
        params.append(start)
    if end:
        where.append("timestamp <= ?")
        params.append(end)
    if hours and hours > 0 and not (start or end):
        cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        where.append("timestamp >= ?")
        params.append(cutoff)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 200
    if limit <= 0 or limit > 1000:
        limit = 200

    # Pull a bit more than requested so post-filtering by login still returns
    # a full page when a filter is supplied.
    fetch_limit = limit * 5 if login else limit
    if fetch_limit > 5000:
        fetch_limit = 5000

    cursor.execute(f"""
        SELECT timestamp, employee_id, login, name, from_path, to_path,
               from_rate, predicted_verdict
        FROM move_log
        {where_clause}
        ORDER BY timestamp DESC, id DESC
        LIMIT {fetch_limit}
    """, params)

    rows = cursor.fetchall()

    # Recover logins/names for rows where move_log stored a blank login.
    # Priority 1: the authoritative employee_login_map (Picking Console bridge).
    # Priority 2: the most recent rate_history entry that has a login.
    missing_ids = {r[1] for r in rows if not (r[2] or "")}
    recovered = {}
    if missing_ids:
        qmarks = ",".join("?" for _ in missing_ids)
        # Authoritative bridge map first
        cursor.execute(f"""
            SELECT employee_id, login FROM employee_login_map
            WHERE employee_id IN ({qmarks}) AND login IS NOT NULL AND login <> ''
        """, list(missing_ids))
        for eid, lg in cursor.fetchall():
            recovered[eid] = (lg, "")
        # rate_history fallback for anything still unresolved
        still = [e for e in missing_ids if e not in recovered]
        if still:
            qm2 = ",".join("?" for _ in still)
            cursor.execute(f"""
                SELECT employee_id, login, name FROM rate_history
                WHERE employee_id IN ({qm2})
                  AND login IS NOT NULL AND login <> ''
                ORDER BY recorded_at DESC
            """, still)
            for eid, lg, nm in cursor.fetchall():
                if eid not in recovered:  # first (most recent) wins
                    recovered[eid] = (lg, nm)

    conn.close()

    login_lc = login.lower() if login else None
    result = []
    for r in rows:
        eid = r[1]
        lg = r[2] or ""
        nm = r[3] or ""
        if not lg and eid in recovered:
            rlg, rnm = recovered[eid]
            lg = rlg or lg
            nm = nm or (rnm or "")
        # Apply the login filter after recovery
        if login_lc is not None and lg.lower() != login_lc:
            continue
        result.append({
            "timestamp": r[0],
            "employee_id": eid,
            "login": lg,
            "name": nm,
            "from_path": to_console_path(r[4]),
            "to_path": to_console_path(r[5]),
            "from_rate": r[6],
            # "AUTO-DETECTED" for system-detected moves, else the recommendation verdict
            "source": "auto" if (r[7] or "") == "AUTO-DETECTED" else "manual",
        })
        if len(result) >= limit:
            break

    return result



# The ONLY low-density process paths are the two single-OP console paths.
# Matching is exact (case-insensitive) so HOV NonCon, the FCLM bucket, etc.
# are NOT counted as low-density.
LOW_DENSITY_PATHS = {"PPSingleOPBOD", "PPSingleOPNonCon"}


def _is_bod(path):
    return (path or "").strip().upper() == "PPSINGLEOPBOD"


def _is_noncon(path):
    return (path or "").strip().upper() == "PPSINGLEOPNONCON"


def _is_low_density(path):
    return _is_bod(path) or _is_noncon(path)


def _parse_ts(ts):
    """Parse a stored 'YYYY-MM-DD HH:MM:SS' timestamp; return datetime or None."""
    if not ts:
        return None
    try:
        return datetime.strptime(ts.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def export_moves_csv(start=None, end=None, login=None):
    """
    Build an end-of-shift CSV report of picker path moves.

    One row per picker (login), summarizing moves within the [start, end] window:
        Login
        Total Moves
        Moves To BOD
        Moves To NonCon
        Moves To Low-Density (BOD + NonCon combined)
        Distinct Paths Worked
        Path Durations         e.g. "PPSingleOP: 42m; PPSingleOPVNA: 18m; ..."
        Longest Path (min)
        First Move / Last Move timestamps

    Duration logic: a picker's move at time T means they LEFT from_path at T, so
    the time spent in from_path = T minus when they entered it (the previous
    move's time, or the window start for the first segment). The final/current
    path runs from its entry time to the window end (or now, if end is in the
    future / unset).

    Args:
        start, end: optional window bounds ("YYYY-MM-DD HH:MM[:SS]" or ISO).
        login: optional single-login filter.

    Returns:
        CSV text (str).
    """
    import csv
    import io

    # Pull all moves in the window (newest-first from get_recent_moves).
    moves = get_recent_moves(login=login, start=start, end=end, limit=1000)

    # Window bounds as datetimes for duration math.
    start_dt = _parse_ts(start.replace("T", " ") if start else None) if start else None
    end_dt = _parse_ts(end.replace("T", " ") if end else None) if end else None
    now = datetime.now()
    # If end is unset or in the future, cap "current path" duration at now.
    effective_end = end_dt if (end_dt and end_dt <= now) else now
    # Safety cap for any single path segment (minutes). Prevents stale data or an
    # unbounded (all-time) export from producing multi-day durations. One shift
    # is well under this, so real shift reports are never affected.
    MAX_SEGMENT_MIN = 24 * 60

    # Group by login (fall back to employee_id), oldest-first for duration math.
    by_login = {}
    for m in moves:
        who = m.get("login") or m.get("employee_id") or "(unknown)"
        by_login.setdefault(who, []).append(m)
    for who in by_login:
        by_login[who].sort(key=lambda x: x.get("timestamp") or "")

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "Login", "Total Moves", "Moves To BOD", "Moves To NonCon",
        "Moves To Low-Density (BOD+NonCon)", "Distinct Paths Worked",
        "Path Durations (minutes)", "Longest Path (min)",
        "First Move", "Last Move",
    ])

    for who in sorted(by_login.keys()):
        picker_moves = by_login[who]
        total = len(picker_moves)
        to_bod = sum(1 for m in picker_moves if _is_bod(m.get("to_path")))
        to_noncon = sum(1 for m in picker_moves if _is_noncon(m.get("to_path")))
        to_low = sum(1 for m in picker_moves if _is_low_density(m.get("to_path")))

        # Build path->minutes durations from consecutive move timestamps.
        durations = {}  # path -> total minutes
        prev_entry = start_dt  # when they entered the first from_path (window start if known)
        for m in picker_moves:
            t = _parse_ts(m.get("timestamp"))
            from_path = m.get("from_path") or "(unknown)"
            if t and prev_entry and t >= prev_entry:
                mins = (t - prev_entry).total_seconds() / 60.0
                if mins > MAX_SEGMENT_MIN:
                    mins = MAX_SEGMENT_MIN
                durations[from_path] = durations.get(from_path, 0.0) + mins
            # After this move they entered to_path at time t
            prev_entry = t
        # Final/current path: from last move time to effective_end
        if picker_moves:
            last_to = picker_moves[-1].get("to_path") or "(unknown)"
            last_t = _parse_ts(picker_moves[-1].get("timestamp"))
            if last_t and effective_end >= last_t:
                mins = (effective_end - last_t).total_seconds() / 60.0
                if mins > MAX_SEGMENT_MIN:
                    mins = MAX_SEGMENT_MIN
                durations[last_to] = durations.get(last_to, 0.0) + mins

        distinct_paths = len(durations)
        dur_str = "; ".join(f"{p}: {round(mn)}m" for p, mn in sorted(durations.items(), key=lambda x: -x[1]))
        longest = round(max(durations.values())) if durations else 0

        first_move = picker_moves[0].get("timestamp", "") if picker_moves else ""
        last_move = picker_moves[-1].get("timestamp", "") if picker_moves else ""

        writer.writerow([
            who, total, to_bod, to_noncon, to_low, distinct_paths,
            dur_str, longest, first_move, last_move,
        ])

    return out.getvalue()
