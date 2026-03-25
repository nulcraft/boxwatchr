from flask import render_template, request, redirect, session, url_for
from boxwatchr import config, imap
from boxwatchr.web.app import app, _require_auth, _require_csrf, _save_app_config, _TLS_MODES, _LEVELS

@app.route("/config", methods=["GET"])
@_require_auth
def config_page():
    account = {
        "id": config.ACCOUNT_ID,
        "name": config.ACCOUNT_NAME,
        "host": config.IMAP_HOST,
        "port": config.IMAP_PORT,
        "username": config.IMAP_USERNAME,
        "folder": config.IMAP_FOLDER,
        "tls_mode": config.IMAP_TLS_MODE,
    }
    folders = imap.get_folder_list() if config.SETUP_COMPLETE else []
    return render_template(
        "config.html",
        account=account,
        folders=folders,
        levels=_LEVELS,
        tls_modes=_TLS_MODES,
        log_level=config.LOG_LEVEL,
        dry_run=config.DRYRUN,
        db_prune_days=config.DB_PRUNE_DAYS,
        check_for_updates=config.CHECK_FOR_UPDATES,
        has_password=bool(config.WEB_PASSWORD),
        tls_mode=config.IMAP_TLS_MODE,
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/config", methods=["POST"])
@_require_auth
@_require_csrf
def config_save():
    old_password_hash = config.WEB_PASSWORD
    new_password_hash = _save_app_config(request.form)
    config.reload()
    if config.SETUP_COMPLETE:
        imap.request_reconnect()
    if new_password_hash != old_password_hash:
        session.clear()
        return redirect(url_for("login"))
    return redirect(url_for("config_page"))
