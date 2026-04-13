import json
import uuid
from flask import render_template, request, redirect, url_for, abort, session
from boxwatchr import config, imap
from boxwatchr.crypto import encrypt_password
from boxwatchr.database import get_all_accounts, get_account, upsert_account, delete_account, update_account_enabled
from boxwatchr.web.app import app, _require_auth, _require_csrf, _TLS_MODES, logger


@app.route("/accounts")
@_require_auth
def accounts_list():
    rows = get_all_accounts()
    accounts = []
    for row in rows:
        accounts.append({
            "id": row["id"],
            "name": row["name"],
            "host": row["host"],
            "port": row["port"],
            "username": row["username"],
            "folder": row["folder"],
            "tls_mode": row["tls_mode"],
            "enabled": row["enabled"],
        })
    return render_template(
        "accounts.html",
        accounts=accounts,
        show_logout=bool(config.WEB_PASSWORD),
    )


@app.route("/accounts/new", methods=["GET", "POST"])
@_require_auth
def account_new():
    error = None
    account = {"name": "", "host": "", "port": 993, "username": "", "folder": "INBOX", "tls_mode": "ssl"}
    folders = []

    if request.method == "POST":
        from boxwatchr.web.app import _check_csrf
        _check_csrf()
        account, error = _validate_account_form(request.form, is_new=True)
        if not error:
            account_id = str(uuid.uuid4())
            encrypted = encrypt_password(request.form.get("imap_password", ""))
            try:
                port_int = int(request.form.get("imap_port", "993"))
            except ValueError:
                port_int = 993
            upsert_account(
                account_id=account_id,
                name=account["name"],
                host=account["host"],
                port=port_int,
                username=account["username"],
                password=encrypted,
                folder=account["folder"],
                poll_interval=60,
                tls_mode=account["tls_mode"],
            )
            config.reload()
            logger.info("User created account '%s'", account["name"])
            return redirect(url_for("accounts_list"))

    return render_template(
        "account_form.html",
        account=account,
        form_action=url_for("account_new"),
        form_title="Add Account",
        error=error,
        folders=folders,
        tls_modes=_TLS_MODES,
        is_new=True,
        show_logout=bool(config.WEB_PASSWORD),
    )


@app.route("/accounts/<account_id>/edit", methods=["GET", "POST"])
@_require_auth
def account_edit(account_id):
    row = get_account(account_id)
    if row is None:
        abort(404)

    error = None
    account = {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "username": row["username"],
        "folder": row["folder"],
        "tls_mode": row["tls_mode"],
    }

    folders = []
    if config.SETUP_COMPLETE:
        try:
            from boxwatchr.crypto import decrypt_password
            acct_dict = dict(account)
            acct_dict["password"] = decrypt_password(row["password"])
            folders = imap.get_folder_list(account=acct_dict)
        except Exception:
            pass

    if request.method == "POST":
        from boxwatchr.web.app import _check_csrf
        _check_csrf()
        account, error = _validate_account_form(request.form, is_new=False)
        account["id"] = account_id
        if not error:
            new_password = request.form.get("imap_password", "")
            encrypted = encrypt_password(new_password) if new_password else row["password"]
            try:
                port_int = int(request.form.get("imap_port", "993"))
            except ValueError:
                port_int = 993
            upsert_account(
                account_id=account_id,
                name=account["name"],
                host=account["host"],
                port=port_int,
                username=account["username"],
                password=encrypted,
                folder=account["folder"],
                poll_interval=60,
                tls_mode=account["tls_mode"],
            )
            config.reload()
            logger.info("User updated account '%s'", account["name"])
            return redirect(url_for("accounts_list"))

    return render_template(
        "account_form.html",
        account=account,
        form_action=url_for("account_edit", account_id=account_id),
        form_title="Edit Account",
        error=error,
        folders=folders,
        tls_modes=_TLS_MODES,
        is_new=False,
        show_logout=bool(config.WEB_PASSWORD),
    )


@app.route("/accounts/<account_id>/delete", methods=["POST"])
@_require_auth
@_require_csrf
def account_delete(account_id):
    row = get_account(account_id)
    if row is None:
        abort(404)
    acct_name = row["name"]
    try:
        delete_account(account_id)
        config.reload()
        logger.info("User deleted account '%s'", acct_name)
    except Exception as e:
        logger.error("Failed to delete account '%s': %s", acct_name, e)
    return redirect(url_for("accounts_list"))


@app.route("/accounts/<account_id>/toggle", methods=["POST"])
@_require_auth
@_require_csrf
def account_toggle(account_id):
    row = get_account(account_id)
    if row is None:
        abort(404)
    new_state = 0 if row["enabled"] else 1
    update_account_enabled(account_id, new_state)
    config.reload()
    label = "enabled" if new_state else "disabled"
    logger.info("User %s account '%s'", label, row["name"])
    return redirect(url_for("accounts_list"))


@app.route("/accounts/select/<account_id>", methods=["POST"])
@_require_auth
@_require_csrf
def account_select(account_id):
    if account_id == "all":
        session.pop("selected_account_id", None)
    else:
        row = get_account(account_id)
        if row is None:
            abort(404)
        session["selected_account_id"] = account_id
    return redirect(request.referrer or url_for("dashboard"))


def _validate_account_form(form, is_new=False):
    account = {
        "name": form.get("account_name", "").strip() or "Default",
        "host": form.get("imap_host", "").strip(),
        "port": form.get("imap_port", "993").strip(),
        "username": form.get("imap_username", "").strip(),
        "folder": form.get("imap_folder", "").strip(),
        "tls_mode": form.get("tls_mode", "ssl").strip(),
    }
    if account["tls_mode"] not in _TLS_MODES:
        account["tls_mode"] = "ssl"

    errors = []
    if not account["host"]:
        errors.append("IMAP host is required.")
    if not account["username"]:
        errors.append("Username is required.")
    if is_new and not form.get("imap_password", ""):
        errors.append("Password is required for new accounts.")
    if not account["folder"]:
        errors.append("Watch folder is required.")

    return account, " ".join(errors) if errors else None
