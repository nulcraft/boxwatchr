import json
import threading
import time
import urllib.request
from flask import jsonify
from boxwatchr import config
from boxwatchr.web.app import app, APP_VERSION

_cache_lock = threading.Lock()
_cache_value = None
_cache_time = 0
_cache_fetching = False
_CACHE_TTL = 3600

_GITHUB_RELEASES_URL = "https://api.github.com/repos/nulcraft/boxwatchr/releases/latest"

def _fetch_latest():
    global _cache_value, _cache_time, _cache_fetching
    now = time.monotonic()
    with _cache_lock:
        if _cache_value is not None and now - _cache_time < _CACHE_TTL:
            return _cache_value
        if _cache_fetching:
            return _cache_value
        _cache_fetching = True
    try:
        req = urllib.request.Request(_GITHUB_RELEASES_URL, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            latest = data["tag_name"].lstrip("v")
            release_notes = data.get("body", "")
        with _cache_lock:
            _cache_value = (latest, release_notes)
            _cache_time = time.monotonic()
        return _cache_value
    except Exception:
        return None
    finally:
        with _cache_lock:
            _cache_fetching = False

@app.route("/api/version/check")
def version_check():
    if not config.CHECK_FOR_UPDATES:
        return jsonify({"current": APP_VERSION, "latest": None, "update_available": False, "release_notes": ""})
    result = _fetch_latest()
    if result is None:
        return jsonify({"current": APP_VERSION, "latest": None, "update_available": False, "release_notes": ""})
    latest, release_notes = result
    try:
        current_parts = tuple(int(x) for x in APP_VERSION.split("."))
        latest_parts = tuple(int(x) for x in latest.split("."))
        update_available = latest_parts > current_parts
    except ValueError:
        update_available = False
    return jsonify({"current": APP_VERSION, "latest": latest, "update_available": update_available, "release_notes": release_notes})
