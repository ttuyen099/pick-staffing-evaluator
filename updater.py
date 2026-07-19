"""PickMatrix Auto-Updater - downloads latest files from GitHub (no cache)."""
import requests
import os
import time
import base64

REPO = "ttuyen099/pick-staffing-evaluator"
API_URL = f"https://api.github.com/repos/{REPO}/contents"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main"

UPDATE_FILES = [
    "staffing_dashboard_server.py",
    "staffing_dashboard.html",
    "fclm_rate_puller.py",
    "cross_training.py",
    "rate_history.py",
    "learning_engine.py",
    "version.txt",
    "updater.py",
    "Start Dashboard.bat",
]

def get_version_remote():
    """Get remote version using API (no cache)."""
    try:
        r = requests.get(f"{API_URL}/version.txt", timeout=10)
        if r.status_code == 200:
            return base64.b64decode(r.json()['content']).decode().strip()
    except:
        pass
    # Fallback to raw with cache-bust
    try:
        r = requests.get(f"{RAW_URL}/version.txt?t={int(time.time())}", timeout=10)
        if r.status_code == 200:
            return r.text.strip()
    except:
        pass
    return None

def update():
    """Download all updatable files."""
    print("  Downloading updates...")
    updated = 0
    for f in UPDATE_FILES:
        try:
            # Use cache-busted raw URL
            r = requests.get(f"{RAW_URL}/{f}?t={int(time.time())}", timeout=15)
            if r.status_code == 200:
                with open(f, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(r.text)
                updated += 1
        except:
            pass
    print(f"  Updated {updated} files.")

if __name__ == "__main__":
    update()
