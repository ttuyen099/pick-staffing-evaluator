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
    "login_lookup.py",
    "version.txt",
    "updater.py",
    "Start Dashboard.bat",
    "README.txt",
    "CHANGELOG.md",
    "sites/HOU8.yaml",
    "sites/CLT3.yaml",
    "sites/LAS6.yaml",
    "sites/MDT4.yaml",
    "sites/MCE1.yaml",
    "sites/MDT1.yaml",
    "sites/PIT2.yaml",
    "sites/ORD2.yaml",
    "sites/OKC2.yaml",
    "sites/SNA4.yaml",
    "sites/MKC4.yaml",
    "sites/FAT2.yaml",
    "sites/SAT4.yaml",
    "sites/LGB6.yaml",
    "sites/LFT1.yaml",
    "sites/MEX6.yaml",
    "sites/MEX2.yaml",
    "sites/BJX1.yaml",
    "sites/PHX7.yaml",
    "sites/DEN8.yaml",
    "sites/CMH2.yaml",
    "sites/GSO1.yaml",
    "sites/GDL1.yaml",
    "sites/MTY1.yaml",
    "sites/MTY3.yaml",
    "sites/SMF6.yaml",
    "sites/LIT2.yaml",
    "sites/MDW6.yaml",
    "sites/CHA2.yaml",
    "sites/TEB3.yaml",
    "sites/CMH3.yaml",
    "sites/GDL2.yaml",
    "sites/HMO3.yaml",
    "sites/MID1.yaml",
    "sites/MEX1.yaml",
    "sites/TIJ1.yaml",
    "sites/MEX3.yaml",
    "sites/MTY2.yaml",
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
                # Create parent directory for nested files (e.g. sites/CLT3.yaml)
                parent = os.path.dirname(f)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(f, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(r.text)
                updated += 1
        except:
            pass
    print(f"  Updated {updated} files.")

if __name__ == "__main__":
    update()
