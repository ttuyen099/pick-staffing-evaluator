"""
Historical Rate Database - Track associate performance over time.

Stores per-associate, per-path, per-shift rate data in SQLite.
Provides 14-day rolling history for staffing move recommendations.
"""

import sqlite3
import os
import logging
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)

# Store DB in a fixed location per user (survives folder moves/updates)
_DB_DIR = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'PickMatrix')
os.makedirs(_DB_DIR, exist_ok=True)
DB_PATH = os.path.join(_DB_DIR, "rate_history.db")
RETENTION_DAYS = 30


def get_db():
    """Get a database connection, creating tables if needed."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            shift TEXT NOT NULL,
            employee_id TEXT NOT NULL,
            login TEXT,
            name TEXT,
            process_path TEXT NOT NULL,
            rate REAL NOT NULL,
            units INTEGER NOT NULL,
            hours REAL NOT NULL,
            recorded_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rate_history_employee
        ON rate_history(employee_id, date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rate_history_date
        ON rate_history(date)
    """)
    conn.commit()
    return conn


def record_shift_data(associates_by_path):
    """
    Record current shift data for all active associates.
    
    Called on each dashboard refresh. Only stores associates with hours > 0.
    Deduplicates: only one entry per associate per path per date per shift.
    """
    if not associates_by_path:
        return
    
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    shift = "Night" if now.hour >= 18 or now.hour < 7 else "Day"
    recorded_at = now.strftime("%Y-%m-%d %H:%M:%S")
    
    conn = get_db()
    cursor = conn.cursor()
    
    inserted = 0
    updated = 0
    
    for path_name, associates in associates_by_path.items():
        for a in associates:
            hours = a.get("paid_hours", 0) or 0
            rate = a.get("rate", 0) or 0
            units = a.get("units", 0) or 0
            employee_id = a.get("employee_id", "")
            
            # Skip if not actively working
            if hours <= 0 or rate <= 0:
                continue
            
            if not employee_id:
                continue
            
            login = a.get("login", "")
            name = a.get("name", "")
            
            # Check if we already have an entry for this associate/path/date/shift
            cursor.execute("""
                SELECT id, rate, units, hours FROM rate_history
                WHERE employee_id = ? AND process_path = ? AND date = ? AND shift = ?
            """, (employee_id, path_name, date_str, shift))
            
            existing = cursor.fetchone()
            
            if existing:
                # Update with latest data (rate accumulates over the shift)
                cursor.execute("""
                    UPDATE rate_history 
                    SET rate = ?, units = ?, hours = ?, recorded_at = ?, login = ?, name = ?
                    WHERE id = ?
                """, (rate, units, hours, recorded_at, login, name, existing[0]))
                updated += 1
            else:
                # Insert new record
                cursor.execute("""
                    INSERT INTO rate_history (date, shift, employee_id, login, name, process_path, rate, units, hours, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (date_str, shift, employee_id, login, name, path_name, rate, units, hours, recorded_at))
                inserted += 1
    
    conn.commit()
    conn.close()
    
    logger.info("History recorded: %d new, %d updated", inserted, updated)


def purge_old_data():
    """Remove data older than RETENTION_DAYS."""
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rate_history WHERE date < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        logger.info("Purged %d records older than %s", deleted, cutoff)


def get_associate_history(employee_id, days=14):
    """
    Get historical rate data for a specific associate.
    
    Returns list of records sorted by date (newest first):
    [{"date", "shift", "process_path", "rate", "units", "hours"}, ...]
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT date, shift, process_path, rate, units, hours, login, name
        FROM rate_history
        WHERE employee_id = ? AND date >= ?
        ORDER BY date DESC, shift DESC
    """, (employee_id, cutoff))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "date": r[0],
            "shift": r[1],
            "process_path": r[2],
            "rate": r[3],
            "units": r[4],
            "hours": r[5],
            "login": r[6],
            "name": r[7],
        }
        for r in rows
    ]


def get_associate_path_averages(employee_id, days=14):
    """
    Get average rate per process path for an associate over last N days.
    
    Returns dict: {path_name: {"avg_rate", "total_shifts", "total_hours", "total_units", "min_rate", "max_rate"}}
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT process_path, 
               AVG(rate) as avg_rate,
               COUNT(*) as shifts,
               SUM(hours) as total_hours,
               SUM(units) as total_units,
               MIN(rate) as min_rate,
               MAX(rate) as max_rate
        FROM rate_history
        WHERE employee_id = ? AND date >= ?
        GROUP BY process_path
    """, (employee_id, cutoff))
    
    rows = cursor.fetchall()
    conn.close()
    
    result = {}
    for r in rows:
        result[r[0]] = {
            "avg_rate": round(r[1], 1),
            "total_shifts": r[2],
            "total_hours": round(r[3], 2),
            "total_units": r[4],
            "min_rate": round(r[5], 1),
            "max_rate": round(r[6], 1),
        }
    
    return result


def recommend_move(employee_id, from_path, to_path, goal_uph, days=14):
    """
    Recommend whether moving an associate is a good staffing decision.
    
    Analyzes:
    1. Their historical rate on the destination path (if any)
    2. Their current rate vs their own historical average
    3. Whether they can likely hit the goal on the destination path
    
    Returns a recommendation dict.
    """
    history = get_associate_path_averages(employee_id, days)
    
    from_stats = history.get(from_path)
    to_stats = history.get(to_path)
    
    recommendation = {
        "employee_id": employee_id,
        "from_path": from_path,
        "to_path": to_path,
        "goal_uph": goal_uph,
        "has_history_on_dest": to_stats is not None,
        "verdict": "UNKNOWN",
        "confidence": "low",
        "reasoning": [],
    }
    
    if to_stats:
        # They have history on the destination path
        recommendation["dest_avg_rate"] = to_stats["avg_rate"]
        recommendation["dest_shifts_worked"] = to_stats["total_shifts"]
        recommendation["dest_min_rate"] = to_stats["min_rate"]
        recommendation["dest_max_rate"] = to_stats["max_rate"]
        
        if to_stats["avg_rate"] >= goal_uph:
            recommendation["verdict"] = "GOOD MOVE"
            recommendation["confidence"] = "high" if to_stats["total_shifts"] >= 3 else "medium"
            recommendation["reasoning"].append(
                f"Historically averages {to_stats['avg_rate']:.1f} UPH on {to_path} "
                f"({to_stats['total_shifts']} shifts) — above goal of {goal_uph}"
            )
        elif to_stats["avg_rate"] >= goal_uph * 0.9:
            recommendation["verdict"] = "OKAY MOVE"
            recommendation["confidence"] = "medium"
            recommendation["reasoning"].append(
                f"Averages {to_stats['avg_rate']:.1f} UPH on {to_path} — close to goal of {goal_uph} "
                f"({((to_stats['avg_rate']/goal_uph)*100):.0f}%)"
            )
        else:
            recommendation["verdict"] = "RISKY MOVE"
            recommendation["confidence"] = "high" if to_stats["total_shifts"] >= 3 else "medium"
            recommendation["reasoning"].append(
                f"Only averages {to_stats['avg_rate']:.1f} UPH on {to_path} — "
                f"below goal of {goal_uph} ({((to_stats['avg_rate']/goal_uph)*100):.0f}%)"
            )
        
        # Add consistency note
        spread = to_stats["max_rate"] - to_stats["min_rate"]
        if spread > goal_uph * 0.3:
            recommendation["reasoning"].append(
                f"Inconsistent: range {to_stats['min_rate']:.1f} - {to_stats['max_rate']:.1f} UPH"
            )
        else:
            recommendation["reasoning"].append(
                f"Consistent performer: {to_stats['min_rate']:.1f} - {to_stats['max_rate']:.1f} UPH range"
            )
    else:
        # No history on destination path
        recommendation["verdict"] = "NO DATA"
        recommendation["confidence"] = "low"
        recommendation["reasoning"].append(
            f"No historical data on {to_path} in last {days} days"
        )
        
        # Use their overall performance as a proxy
        if from_stats:
            recommendation["reasoning"].append(
                f"Currently averaging {from_stats['avg_rate']:.1f} UPH on {from_path}"
            )
    
    # Add source path context
    if from_stats:
        recommendation["source_avg_rate"] = from_stats["avg_rate"]
        recommendation["source_shifts_worked"] = from_stats["total_shifts"]
    
    return recommendation


def get_all_history_summary(days=14):
    """
    Get a summary of all associates with historical data.
    Used by the dashboard to show history indicators and hover tooltips.
    
    Returns: {employee_id: {paths_worked: [...], total_shifts: N, days_active: N, path_rates: {path: avg_rate}}}
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_db()
    cursor = conn.cursor()
    
    # Get basic summary per employee
    cursor.execute("""
        SELECT employee_id, 
               GROUP_CONCAT(DISTINCT process_path) as paths,
               COUNT(*) as total_entries,
               COUNT(DISTINCT date) as days_active
        FROM rate_history
        WHERE date >= ?
        GROUP BY employee_id
    """, (cutoff,))
    
    rows = cursor.fetchall()
    
    result = {}
    for r in rows:
        result[r[0]] = {
            "paths_worked": r[1].split(",") if r[1] else [],
            "total_entries": r[2],
            "days_active": r[3],
            "path_rates": {},
        }
    
    # Get avg rate per path per employee
    cursor.execute("""
        SELECT employee_id, process_path, ROUND(AVG(rate), 1) as avg_rate, COUNT(*) as shifts
        FROM rate_history
        WHERE date >= ?
        GROUP BY employee_id, process_path
    """, (cutoff,))
    
    for r in cursor.fetchall():
        eid = r[0]
        if eid in result:
            result[eid]["path_rates"][r[1]] = {"avg_rate": r[2], "shifts": r[3]}
    
    conn.close()
    
    return result


if __name__ == "__main__":
    """Run standalone to inspect the database."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM rate_history")
    total = cursor.fetchone()[0]
    print(f"Total records in database: {total}")
    
    if total > 0:
        cursor.execute("SELECT DISTINCT date FROM rate_history ORDER BY date DESC LIMIT 14")
        dates = [r[0] for r in cursor.fetchall()]
        print(f"Dates with data: {dates}")
        
        cursor.execute("""
            SELECT employee_id, login, name, COUNT(DISTINCT date) as days, COUNT(DISTINCT process_path) as paths
            FROM rate_history
            GROUP BY employee_id
            ORDER BY days DESC
            LIMIT 20
        """)
        print("\nTop associates by days active:")
        for r in cursor.fetchall():
            print(f"  {r[2] or r[1] or r[0]}: {r[3]} days, {r[4]} paths")
    else:
        print("No data yet. Run the dashboard server to start collecting.")
    
    conn.close()
