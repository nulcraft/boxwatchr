import json
from flask import render_template, request, redirect, url_for, abort, flash, session
from boxwatchr import config
from boxwatchr.database import get_presets, get_preset, get_account_presets, set_account_preset, get_all_accounts, upsert_preset, delete_preset as db_delete_preset
from boxwatchr.presets import sync_preset_rules, BUILT_IN_PRESETS
from boxwatchr.rules import load_rules
from boxwatchr.web.app import app, _require_auth, _require_csrf, logger


def _get_active_account_id():
    """Get the account ID to use for preset operations."""
    acct_id = session.get("selected_account_id")
    if acct_id:
        return acct_id
    return config.ACCOUNT_ID


@app.route("/presets")
@_require_auth
def presets_list():
    account_id = _get_active_account_id()
    rows = get_account_presets(account_id)

    presets = []
    for row in rows:
        patterns = json.loads(row["patterns"]) if isinstance(row["patterns"], str) else row["patterns"]
        default_actions = json.loads(row["default_actions"]) if isinstance(row["default_actions"], str) else row["default_actions"]
        presets.append({
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "category": row["category"],
            "patterns": patterns,
            "default_actions": default_actions,
            "built_in": row["built_in"],
            "enabled": bool(row["ap_enabled"]) if row["ap_enabled"] is not None else False,
        })

    accounts = get_all_accounts()
    return render_template(
        "presets.html",
        presets=presets,
        account_id=account_id,
        accounts=[{"id": a["id"], "name": a["name"]} for a in accounts],
        show_logout=bool(config.WEB_PASSWORD),
    )


@app.route("/presets/<preset_id>")
@_require_auth
def preset_detail(preset_id):
    row = get_preset(preset_id)
    if row is None:
        abort(404)

    account_id = _get_active_account_id()
    patterns = json.loads(row["patterns"]) if isinstance(row["patterns"], str) else row["patterns"]
    default_actions = json.loads(row["default_actions"]) if isinstance(row["default_actions"], str) else row["default_actions"]

    preset = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "patterns": patterns,
        "default_actions": default_actions,
        "built_in": row["built_in"],
    }

    return render_template(
        "preset_detail.html",
        preset=preset,
        patterns_json=json.dumps(patterns, indent=2),
        actions_json=json.dumps(default_actions, indent=2),
        account_id=account_id,
        show_logout=bool(config.WEB_PASSWORD),
    )


@app.route("/presets/<preset_id>/toggle", methods=["POST"])
@_require_auth
@_require_csrf
def preset_toggle(preset_id):
    row = get_preset(preset_id)
    if row is None:
        abort(404)

    account_id = _get_active_account_id()
    if not account_id:
        flash("Select an account first.", "danger")
        return redirect(url_for("presets_list"))

    # Check current state
    account_presets = get_account_presets(account_id)
    current_enabled = False
    current_override = None
    for ap in account_presets:
        if ap["id"] == preset_id:
            current_enabled = bool(ap["ap_enabled"])
            current_override = ap["actions_override"]
            break

    new_enabled = not current_enabled
    set_account_preset(account_id, preset_id, new_enabled, current_override)
    sync_preset_rules(account_id, preset_id, new_enabled, current_override)
    load_rules(account_id)

    label = "enabled" if new_enabled else "disabled"
    logger.info("User %s preset '%s' for account %s", label, row["name"], account_id)
    flash("Preset '%s' %s." % (row["name"], label), "success")
    return redirect(url_for("presets_list"))


@app.route("/presets/<preset_id>/edit", methods=["GET", "POST"])
@_require_auth
def preset_edit(preset_id):
    row = get_preset(preset_id)
    if row is None:
        abort(404)

    error = None
    patterns = json.loads(row["patterns"]) if isinstance(row["patterns"], str) else row["patterns"]
    default_actions = json.loads(row["default_actions"]) if isinstance(row["default_actions"], str) else row["default_actions"]

    preset = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "patterns": patterns,
        "default_actions": default_actions,
        "built_in": row["built_in"],
    }

    if request.method == "POST":
        from boxwatchr.web.app import _check_csrf
        _check_csrf()

        new_name = request.form.get("name", "").strip()
        new_description = request.form.get("description", "").strip()
        patterns_raw = request.form.get("patterns_json", "").strip()
        actions_raw = request.form.get("actions_json", "").strip()

        if not new_name:
            error = "Name is required."
        else:
            try:
                new_patterns = json.loads(patterns_raw)
                if not isinstance(new_patterns, list):
                    error = "Patterns must be a JSON array."
            except (json.JSONDecodeError, ValueError) as e:
                error = "Invalid patterns JSON: %s" % e

        if not error:
            try:
                new_actions = json.loads(actions_raw)
                if not isinstance(new_actions, list):
                    error = "Actions must be a JSON array."
            except (json.JSONDecodeError, ValueError) as e:
                error = "Invalid actions JSON: %s" % e

        if not error:
            upsert_preset(
                preset_id=preset_id,
                name=new_name,
                description=new_description,
                category=preset["category"],
                patterns_json=json.dumps(new_patterns),
                default_actions_json=json.dumps(new_actions),
                built_in=preset["built_in"],
            )
            # Re-sync rules for all accounts that have this preset enabled
            for acct in get_all_accounts():
                acct_presets = get_account_presets(acct["id"])
                for ap in acct_presets:
                    if ap["id"] == preset_id and ap["ap_enabled"]:
                        sync_preset_rules(acct["id"], preset_id, True, ap["actions_override"])
                        load_rules(acct["id"])

            logger.info("User updated preset '%s'", new_name)
            flash("Preset '%s' updated." % new_name, "success")
            return redirect(url_for("presets_list"))
        else:
            preset["name"] = new_name
            preset["description"] = new_description

    return render_template(
        "preset_edit.html",
        preset=preset,
        patterns_json=json.dumps(preset["patterns"], indent=2),
        actions_json=json.dumps(preset["default_actions"], indent=2),
        error=error,
        show_logout=bool(config.WEB_PASSWORD),
    )


@app.route("/presets/<preset_id>/delete", methods=["POST"])
@_require_auth
@_require_csrf
def preset_delete(preset_id):
    row = get_preset(preset_id)
    if row is None:
        abort(404)
    if row["built_in"]:
        flash("Built-in presets cannot be deleted.", "danger")
        return redirect(url_for("presets_list"))

    name = row["name"]
    db_delete_preset(preset_id)
    logger.info("User deleted preset '%s'", name)
    flash("Preset '%s' deleted." % name, "success")
    return redirect(url_for("presets_list"))
