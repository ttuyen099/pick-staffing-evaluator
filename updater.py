import requests, os
REPO = "ttuyen099/pick-staffing-evaluator"
BASE_URL = f"https://raw.githubusercontent.com/{REPO}/main"
UPDATE_FILES = ["staffing_dashboard_server.py","staffing_dashboard.html","fclm_rate_puller.py","cross_training.py","rate_history.py","learning_engine.py","version.txt","updater.py","Start Dashboard.bat"]
def update():
    for f in UPDATE_FILES:
        try:
            r = requests.get(f"{BASE_URL}/{f}", timeout=10)
            if r.status_code == 200:
                with open(f, 'w', encoding='utf-8', newline='\n') as fh: fh.write(r.text)
        except: pass
if __name__ == "__main__": update()
