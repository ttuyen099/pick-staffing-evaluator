"""
PickMatrix Auto-Updater - downloads the latest files from GitHub.

Reliability design (2.5.7):
  * ALL files are downloaded to a temporary staging directory first. Nothing in
    the install is touched until every file has downloaded successfully. A
    network drop part-way through therefore leaves the install exactly as it
    was, instead of a half-new/half-old mix of versions.
  * version.txt is written LAST, only after every other file is in place. The
    launcher's "update only if GitHub is strictly newer" check keys off this
    file, so writing it early would have made a broken partial update look
    complete and permanently skip the repair on the next launch.
  * Each file is moved into place with os.replace() (atomic on the same
    volume), so a crash mid-write cannot leave a truncated file behind.
  * Failures are reported per file and the process exits non-zero, so the
    launcher / in-app updater can tell the difference between "updated" and
    "nothing happened".
"""
import os
import sys
import time
import base64
import shutil
import tempfile

import requests

REPO = "ttuyen099/pick-staffing-evaluator"
API_URL = f"https://api.github.com/repos/{REPO}/contents"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main"

# Always operate on the folder this script lives in, regardless of the CWD the
# caller used. The launcher cd's here anyway; the in-app updater passes cwd.
INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))

# Files to update. version.txt is handled separately and written last.
UPDATE_FILES = [
    "staffing_dashboard_server.py",
    "staffing_dashboard.html",
    "fclm_rate_puller.py",
    "cross_training.py",
    "rate_history.py",
    "learning_engine.py",
    "login_lookup.py",
    "idle_time.py",
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
    "sites/ACY2.yaml",
    "sites/SBD2.yaml",
    "sites/FOE1.yaml",
    "sites/JVL1.yaml",
    "sites/AKR1.yaml",
    "sites/PDX7.yaml",
]

VERSION_FILE = "version.txt"

# Per-request timeout and a small retry budget for transient blips. The whole
# run must stay well inside the in-app updater's 30s subprocess timeout for the
# common case, but a slow corporate proxy should not turn one hiccup into a
# failed update.
REQUEST_TIMEOUT = 15
RETRIES = 2


def _get(url, timeout=REQUEST_TIMEOUT):
    """GET with a couple of retries on transient network errors / 5xx."""
    last_err = None
    for attempt in range(RETRIES + 1):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            # 4xx (e.g. 404 for a file that no longer exists) will not improve
            # by retrying; 5xx / 429 might.
            if r.status_code < 500 and r.status_code != 429:
                return r
            last_err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last_err = str(e)
        if attempt < RETRIES:
            time.sleep(0.5 * (attempt + 1))
    raise requests.RequestException(last_err or "request failed")


def get_version_remote():
    """
    Return the version string on GitHub main, or None if it cannot be read.

    Prefers the raw URL: it is served from a CDN with no per-IP API rate limit.
    (api.github.com unauthenticated is capped at 60 req/hour per IP, which a
    whole FC behind one NAT exhausts quickly.) Falls back to the API only if
    raw is unreachable.
    """
    try:
        r = _get(f"{RAW_URL}/{VERSION_FILE}?t={int(time.time())}", timeout=10)
        if r.status_code == 200 and r.text.strip():
            return r.text.strip()
    except requests.RequestException:
        pass
    try:
        r = _get(f"{API_URL}/{VERSION_FILE}", timeout=10)
        if r.status_code == 200:
            return base64.b64decode(r.json()["content"]).decode().strip()
    except (requests.RequestException, KeyError, ValueError):
        pass
    return None


def _download_to(url, dest_path):
    """Download url into dest_path (creating parent dirs). Raises on failure."""
    r = _get(url)
    if r.status_code != 200:
        raise requests.RequestException(f"HTTP {r.status_code}")
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # Write bytes exactly as served; normalise to LF like the previous updater
    # did so Windows/Unix checkouts produce identical files.
    text = r.content.decode("utf-8")
    with open(dest_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def update():
    """
    Download every file in UPDATE_FILES plus version.txt into a staging dir,
    then move them into the install atomically with version.txt last.

    Returns the number of files installed. Raises RuntimeError (with a
    per-file summary) if ANY download failed - in that case the install is
    left completely untouched.
    """
    print("  Downloading updates...")
    stage = tempfile.mkdtemp(prefix="pickmatrix_update_", dir=INSTALL_DIR)
    try:
        failures = []
        for f in UPDATE_FILES + [VERSION_FILE]:
            url = f"{RAW_URL}/{f}?t={int(time.time())}"
            try:
                _download_to(url, os.path.join(stage, f))
            except (requests.RequestException, OSError, UnicodeDecodeError) as e:
                failures.append((f, str(e)))
                print(f"    FAILED  {f}: {e}")

        if failures:
            raise RuntimeError(
                f"{len(failures)} of {len(UPDATE_FILES) + 1} files failed to "
                f"download; install left unchanged."
            )

        # Everything downloaded. Move into place: code/configs first,
        # version.txt last so a crash between the two leaves the version
        # pointing at the OLD release (which is still eligible to re-update).
        installed = 0
        for f in UPDATE_FILES:
            src = os.path.join(stage, f)
            dst = os.path.join(INSTALL_DIR, f)
            parent = os.path.dirname(dst)
            if parent:
                os.makedirs(parent, exist_ok=True)
            os.replace(src, dst)
            installed += 1

        os.replace(os.path.join(stage, VERSION_FILE),
                   os.path.join(INSTALL_DIR, VERSION_FILE))
        installed += 1

        print(f"  Updated {installed} files.")
        return installed
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main():
    try:
        update()
        return 0
    except RuntimeError as e:
        print(f"  UPDATE FAILED: {e}")
        return 1
    except OSError as e:
        # e.g. a file locked by a running server, read-only folder, disk full.
        print(f"  UPDATE FAILED (filesystem): {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
