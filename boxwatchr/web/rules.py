import json
from flask import render_template, request, redirect, url_for, abort, flash
from boxwatchr import config
from boxwatchr import rules as _rules_engine
from boxwatchr.database import get_rules, delete_rule, move_rule_up, move_rule_down, insert_rule, duplicate_rule, get_rule_stats
from boxwatchr.rules import validate_rule
from boxwatchr.web.app import app, _require_auth, _require_csrf, logger

_FIELD_LABELS = {
    "sender": "Full address",
    "sender_local": "Username",
    "sender_domain": "Full domain",
    "sender_domain_name": "Subdomain + domain",
    "sender_domain_root": "Domain (no subdomain)",
    "sender_domain_tld": "TLD",
    "recipient": "Full address",
    "recipient_local": "Username",
    "recipient_domain": "Full domain",
    "recipient_domain_name": "Subdomain + domain",
    "recipient_domain_root": "Domain (no subdomain)",
    "recipient_domain_tld": "TLD",
    "subject": "Subject",
    "raw_headers": "Raw headers",
    "attachment_name": "Attachment name",
    "attachment_extension": "Attachment extension",
    "attachment_content_type": "Attachment content type",
    "rspamd_score": "Spam score",
    "email_age_days": "Email age (days)",
    "email_age_hours": "Email age (hours)",
}

_ACTION_LABELS = {
    "move": "Move to folder",
    "mark_read": "Mark as read",
    "mark_unread": "Mark as unread",
    "flag": "Mark as flagged",
    "unflag": "Mark as unflagged",
    "learn_spam": "Submit to rspamd as spam",
    "learn_ham": "Submit to rspamd as ham",
    "notify_discord": "Send Discord notification",
    "add_label": "Add label/tag",
}


@app.route("/rules")
@_require_auth
def rules_list():
    rows = get_rules(config.ACCOUNT_ID)
    rule_stats = get_rule_stats(config.ACCOUNT_ID)
    rules = []
    export = []
    for row in rows:
        conditions = json.loads(row["conditions"] or "[]")
        actions = json.loads(row["actions"] or "[]")
        condition_groups = json.loads(row["condition_groups"] or "[]") if "condition_groups" in row.keys() else []
        name = row["name"]
        stats = rule_stats.get(row["id"], rule_stats.get(name, {"count": 0, "last_triggered": None}))
        rules.append({
            "id": row["id"],
            "name": name,
            "match": row["match"],
            "conditions": conditions,
            "condition_groups": condition_groups,
            "actions": actions,
            "enabled": bool(row["enabled"]) if "enabled" in row.keys() else True,
            "hit_count": stats["count"],
            "last_triggered": stats["last_triggered"],
        })
        export.append({
            "name": row["name"],
            "match": row["match"],
            "conditions": conditions,
            "actions": actions,
        })
    return render_template(
        "rules.html",
        rules=rules,
        export_json=json.dumps(export),
        field_labels=_FIELD_LABELS,
        action_labels=_ACTION_LABELS,
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/rules/<rule_id>/delete", methods=["POST"])
@_require_auth
@_require_csrf
def rule_delete(rule_id):
    from boxwatchr.database import get_rule
    row = get_rule(rule_id)
    if row is None or row["account_id"] != config.ACCOUNT_ID:
        abort(404)
    rule_name = row["name"]
    try:
        delete_rule(rule_id, config.ACCOUNT_ID)
        _rules_engine.load_rules()
        logger.info("User deleted rule '%s'", rule_name)
    except Exception as e:
        logger.error("Failed to delete rule '%s': %s", rule_name, e)
    return redirect(url_for("rules_list"))

@app.route("/rules/<rule_id>/move-up", methods=["POST"])
@_require_auth
@_require_csrf
def rule_move_up(rule_id):
    from boxwatchr.database import get_rule
    row = get_rule(rule_id)
    if row is None or row["account_id"] != config.ACCOUNT_ID:
        abort(404)
    try:
        move_rule_up(rule_id, config.ACCOUNT_ID)
        _rules_engine.load_rules()
    except Exception as e:
        logger.error("Failed to move rule up: %s", e)
    return redirect(url_for("rules_list"))

@app.route("/rules/<rule_id>/move-down", methods=["POST"])
@_require_auth
@_require_csrf
def rule_move_down(rule_id):
    from boxwatchr.database import get_rule
    row = get_rule(rule_id)
    if row is None or row["account_id"] != config.ACCOUNT_ID:
        abort(404)
    try:
        move_rule_down(rule_id, config.ACCOUNT_ID)
        _rules_engine.load_rules()
    except Exception as e:
        logger.error("Failed to move rule down: %s", e)
    return redirect(url_for("rules_list"))

@app.route("/rules/<rule_id>/duplicate", methods=["POST"])
@_require_auth
@_require_csrf
def rule_duplicate(rule_id):
    from boxwatchr.database import get_rule
    row = get_rule(rule_id)
    if row is None or row["account_id"] != config.ACCOUNT_ID:
        abort(404)
    try:
        duplicate_rule(rule_id, config.ACCOUNT_ID)
        _rules_engine.load_rules()
        logger.info("User duplicated rule '%s'", row["name"])
        flash("Rule '%s' duplicated." % row["name"], "success")
    except Exception as e:
        logger.error("Failed to duplicate rule '%s': %s", row["name"], e)
        flash("Failed to duplicate rule.", "danger")
    return redirect(url_for("rules_list"))

@app.route("/rules/<rule_id>/toggle", methods=["POST"])
@_require_auth
@_require_csrf
def rule_toggle(rule_id):
    from boxwatchr.database import get_rule, db_connection
    row = get_rule(rule_id)
    if row is None or row["account_id"] != config.ACCOUNT_ID:
        abort(404)
    try:
        current = bool(row["enabled"]) if "enabled" in row.keys() else True
        new_enabled = 0 if current else 1
        with db_connection() as conn:
            conn.execute("UPDATE rules SET enabled = ? WHERE id = ?", (new_enabled, rule_id))
            conn.commit()
        _rules_engine.load_rules()
        state = "enabled" if new_enabled else "disabled"
        logger.info("User %s rule '%s'", state, row["name"])
    except Exception as e:
        logger.error("Failed to toggle rule '%s': %s", row["name"], e)
    return redirect(url_for("rules_list"))

@app.route("/rules/import", methods=["POST"])
@_require_auth
@_require_csrf
def rules_import():
    raw = request.form.get("rules_json", "").strip()
    if not raw:
        flash("No data provided.", "danger")
        return redirect(url_for("rules_list"))

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        flash("Invalid JSON: %s" % e, "danger")
        return redirect(url_for("rules_list"))

    if not isinstance(data, list):
        flash("Expected a JSON array of rules.", "danger")
        return redirect(url_for("rules_list"))

    existing_rows = get_rules(config.ACCOUNT_ID)
    existing_set = set()
    for row in existing_rows:
        key = (
            row["name"],
            json.dumps(json.loads(row["conditions"] or "[]"), sort_keys=True),
            json.dumps(json.loads(row["actions"] or "[]"), sort_keys=True),
        )
        existing_set.add(key)

    imported = 0
    skipped = 0
    duplicates = 0
    for item in data:
        if not isinstance(item, dict):
            skipped += 1
            continue
        validated = validate_rule(item)
        if validated is None:
            skipped += 1
            continue
        key = (
            validated["name"],
            json.dumps(validated["conditions"], sort_keys=True),
            json.dumps(validated["actions"], sort_keys=True),
        )
        if key in existing_set:
            duplicates += 1
            continue
        try:
            insert_rule(
                account_id=config.ACCOUNT_ID,
                name=validated["name"],
                match=validated["match"],
                conditions_json=json.dumps(validated["conditions"]),
                actions_json=json.dumps(validated["actions"]),
            )
            existing_set.add(key)
            imported += 1
        except Exception as e:
            logger.error("Failed to import rule '%s': %s", validated.get("name", "?"), e)
            skipped += 1

    if imported:
        _rules_engine.load_rules()
        logger.info("User imported %s rule(s) (%s duplicate(s), %s skipped)", imported, duplicates, skipped)

    parts = ["Imported %s rule(s)." % imported]
    if duplicates:
        parts.append("%s already existed and were skipped." % duplicates)
    if skipped:
        parts.append("%s skipped (invalid or missing required fields)." % skipped)
    flash(" ".join(parts), "success")
    return redirect(url_for("rules_list"))
