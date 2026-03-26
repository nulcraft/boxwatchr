import time
import threading
from flask import render_template, request, redirect, session, url_for
from boxwatchr import config
from boxwatchr.web.app import app, _check_csrf, _require_csrf, _check_password, logger

_login_failures = {}
_login_failures_lock = threading.Lock()
_LOGIN_WINDOW = 60.0
_LOGIN_MAX_FAILURES = 5

def _is_rate_limited():
    ip = request.remote_addr or ""
    now = time.monotonic()
    with _login_failures_lock:
        failures = [t for t in _login_failures.get(ip, []) if now - t < _LOGIN_WINDOW]
        _login_failures[ip] = failures
        stale = [k for k, v in _login_failures.items() if k != ip and not any(now - t < _LOGIN_WINDOW for t in v)]
        for k in stale:
            del _login_failures[k]
        return len(failures) >= _LOGIN_MAX_FAILURES

def _record_login_failure():
    ip = request.remote_addr or ""
    now = time.monotonic()
    with _login_failures_lock:
        failures = _login_failures.get(ip, [])
        failures.append(now)
        _login_failures[ip] = failures

@app.route("/login", methods=["GET", "POST"])
def login():
    if not config.SETUP_COMPLETE:
        return redirect(url_for("setup"))
    if not config.WEB_PASSWORD:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        _check_csrf()
        if _is_rate_limited():
            logger.warning("Login rate limit exceeded for %s", request.remote_addr)
            return render_template("login.html", error="Too many failed attempts. Try again in a minute."), 429
        password = request.form.get("password", "")
        if _check_password(password, config.WEB_PASSWORD):
            session["authenticated"] = True
            return redirect(url_for("dashboard"))
        _record_login_failure()
        return render_template("login.html", error="Incorrect password.")

    return render_template("login.html", error=None)

@app.route("/logout", methods=["POST"])
@_require_csrf
def logout():
    if not config.SETUP_COMPLETE:
        return redirect(url_for("setup"))
    session.clear()
    return redirect(url_for("login"))
