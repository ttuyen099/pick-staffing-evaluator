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

DB_PATH = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'PickMatrix', "rate_history.db")


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
    
    conn.commit()
    return conn


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
