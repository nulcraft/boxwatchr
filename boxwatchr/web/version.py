import threading
import time
import urllib.request
from flask import jsonify
from boxwatchr import config
from boxwatchr.web.app import app, APP_VERSION

_cache_lock = threading.Lock()
_cache_value = None
_cache_time = 0
_CACHE_TTL = 3600

_GITHUB_VERSION_URL = "https://raw.githubusercontent.com/nulcraft/boxwatchr/main/VERSION"

def _fetch_latest():
    global _cache_value, _cache_time
    now = time.monotonic()
    with _cache_lock:
        if _cache_value is not None and now - _cache_time < _CACHE_TTL:
            return _cache_value
    try:
        with urllib.request.urlopen(_GITHUB_VERSION_URL, timeout=5) as resp:
            latest = resp.read().decode().strip()
    except Exception:
        return None
    with _cache_lock:
        _cache_value = latest
        _cache_time = time.monotonic()
    return latest

@app.route("/api/version/check")
def version_check():
    if not config.CHECK_FOR_UPDATES:
        return jsonify({"current": APP_VERSION, "latest": None, "update_available": False})
    latest = _fetch_latest()
    if latest is None:
        return jsonify({"current": APP_VERSION, "latest": None, "update_available": False})
    try:
        current_parts = tuple(int(x) for x in APP_VERSION.split("."))
        latest_parts = tuple(int(x) for x in latest.split("."))
        update_available = latest_parts > current_parts
    except ValueError:
        update_available = False
    return jsonify({"current": APP_VERSION, "latest": latest, "update_available": update_available})
