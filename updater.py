"""Auto-updater - downloads latest PickMatrix files from GitHub."""
import requests
import os

REPO = "ttuyen099/pick-staffing-evaluator"
BRANCH = "main"
BASE_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

# Files to update (excludes config files so user settings are preserved)
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

# Files to NEVER overwrite (user config)
PRESERVE_FILES = [
    "config.yaml",
    "staffing_config.yaml",
    "rate_history.db",
]

def update():
    print("  Downloading updates...")
    updated = 0
    failed = 0
    
    for filename in UPDATE_FILES:
        try:
            url = f"{BASE_URL}/{filename}"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                with open(filename, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(r.text)
                updated += 1
            else:
                print(f"    Skip {filename} (not found on server)")
        except Exception as e:
            print(f"    Failed {filename}: {e}")
            failed += 1
    
    print(f"  Updated {updated} files. {'(' + str(failed) + ' failed)' if failed else ''}")

if __name__ == "__main__":
    update()
