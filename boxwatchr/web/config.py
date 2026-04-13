from flask import render_template, request, redirect, session, url_for
from boxwatchr import config, imap
from boxwatchr.database import bulk_set_config, get_first_account
from boxwatchr.web.app import app, _require_auth, _require_csrf, _hash_password, _TLS_MODES, _LEVELS

@app.route("/config", methods=["GET"])
@_require_auth
def config_page():
    return render_template(
        "config.html",
        levels=_LEVELS,
        log_level=config.LOG_LEVEL,
        dry_run=config.DRYRUN,
        db_prune_days=config.DB_PRUNE_DAYS,
        check_for_updates=config.CHECK_FOR_UPDATES,
        has_password=bool(config.WEB_PASSWORD),
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/config", methods=["POST"])
@_require_auth
@_require_csrf
def config_save():
    old_password_hash = config.WEB_PASSWORD

    log_level = request.form.get("log_level", "INFO").strip().upper()
    if log_level not in _LEVELS:
        log_level = "INFO"

    try:
        db_prune_days = int(request.form.get("db_prune_days", "0"))
        if db_prune_days < 0:
            db_prune_days = 0
    except ValueError:
        db_prune_days = 0

    dry_run = request.form.get("dry_run") == "true"
    check_for_updates = request.form.get("check_for_updates") != "false"

    disable_password = request.form.get("disable_password") == "1"
    new_web_password_raw = request.form.get("web_password", "")
    if disable_password:
        web_password_stored = ""
    elif new_web_password_raw:
        web_password_stored = _hash_password(new_web_password_raw)
    else:
        web_password_stored = config.WEB_PASSWORD

    bulk_set_config({
        "log_level": log_level,
        "dry_run": "true" if dry_run else "false",
        "web_password": web_password_stored,
        "db_prune_days": str(db_prune_days),
        "check_for_updates": "true" if check_for_updates else "false",
    })

    config.reload()

    if web_password_stored != old_password_hash:
        session.clear()
        return redirect(url_for("login"))
    return redirect(url_for("config_page"))
