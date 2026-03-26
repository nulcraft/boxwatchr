import json
import sqlite3
from datetime import datetime, timezone
from flask import render_template, request, redirect, url_for, abort, flash
from boxwatchr import config, imap, spam
from boxwatchr.database import db_connection, get_rule, insert_rule, update_rule, enqueue_email_update
from boxwatchr.notes import action_sentence
from boxwatchr.rules import validate_rule, check_rule, load_rules, TERMINAL_ACTIONS
from boxwatchr.web.app import app, _require_auth, _require_csrf, _check_csrf, logger
from boxwatchr.web.rules import _FIELD_LABELS, _ACTION_LABELS


def _parse_rule_form(form):
    condition_fields = form.getlist("condition_field")
    condition_operators = form.getlist("condition_operator")
    condition_values = form.getlist("condition_value")
    action_types = form.getlist("action_type")
    action_destinations = form.getlist("action_destination")

    conditions = []
    for field, operator, value in zip(condition_fields, condition_operators, condition_values):
        if field and operator:
            conditions.append({"field": field, "operator": operator, "value": value})

    actions = []
    dest_idx = 0
    for action_type in action_types:
        if not action_type:
            continue
        action = {"type": action_type}
        if action_type == "move":
            action["destination"] = action_destinations[dest_idx] if dest_idx < len(action_destinations) else ""
            dest_idx += 1
        actions.append(action)

    return {
        "name": form.get("name", "").strip(),
        "match": form.get("match", "all"),
        "conditions": conditions,
        "actions": actions,
    }


@app.route("/rules/new", methods=["GET", "POST"])
@_require_auth
def rule_new():
    error = None
    rule = {"name": "", "match": "all", "conditions": [], "actions": []}
    folders = imap.get_folder_list()

    if request.method == "POST":
        _check_csrf()
        rule = _parse_rule_form(request.form)
        validated = validate_rule(rule)
        if validated is None:
            error = "Rule is invalid. Check that all fields are filled in correctly and try again."
        else:
            try:
                insert_rule(
                    account_id=config.ACCOUNT_ID,
                    name=validated["name"],
                    match=validated["match"],
                    conditions_json=json.dumps(validated["conditions"]),
                    actions_json=json.dumps(validated["actions"]),
                )
                load_rules()
                logger.info("User created rule '%s'", validated["name"])
                return redirect(url_for("rules_list"))
            except Exception as e:
                error = "Failed to save rule: %s" % e

    return render_template(
        "rule_form.html",
        rule=rule,
        form_action=url_for("rule_new"),
        error=error,
        folders=folders,
        field_labels=_FIELD_LABELS,
        action_labels=_ACTION_LABELS,
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/rules/<rule_id>/edit", methods=["GET", "POST"])
@_require_auth
def rule_edit(rule_id):
    row = get_rule(rule_id)
    if row is None or row["account_id"] != config.ACCOUNT_ID:
        abort(404)

    error = None
    rule = {
        "id": row["id"],
        "name": row["name"],
        "match": row["match"],
        "conditions": json.loads(row["conditions"] or "[]"),
        "actions": json.loads(row["actions"] or "[]"),
    }
    folders = imap.get_folder_list()

    if request.method == "POST":
        _check_csrf()
        rule = _parse_rule_form(request.form)
        rule["id"] = rule_id
        validated = validate_rule(rule)
        if validated is None:
            error = "Rule is invalid. Check that all fields are filled in correctly and try again."
        else:
            try:
                update_rule(
                    rule_id=rule_id,
                    name=validated["name"],
                    match=validated["match"],
                    conditions_json=json.dumps(validated["conditions"]),
                    actions_json=json.dumps(validated["actions"]),
                )
                load_rules()
                logger.info("User updated rule '%s'", validated["name"])
                return redirect(url_for("rules_list"))
            except Exception as e:
                error = "Failed to save rule: %s" % e

    return render_template(
        "rule_form.html",
        rule=rule,
        form_action=url_for("rule_edit", rule_id=rule_id),
        error=error,
        folders=folders,
        field_labels=_FIELD_LABELS,
        action_labels=_ACTION_LABELS,
        show_logout=bool(config.WEB_PASSWORD),
    )

@app.route("/rules/<rule_id>/run", methods=["POST"])
@_require_auth
@_require_csrf
def rule_run(rule_id):
    row = get_rule(rule_id)
    if row is None or row["account_id"] != config.ACCOUNT_ID:
        abort(404)

    rule_dict = {
        "name": row["name"],
        "match": row["match"],
        "conditions": json.loads(row["conditions"] or "[]"),
        "actions": json.loads(row["actions"] or "[]"),
    }
    rule = validate_rule(rule_dict)
    if rule is None:
        return redirect(url_for("rules_list"))

    logger.info("Rule '%s' run manually (DRYRUN=%s)", rule["name"], config.DRYRUN)

    matched = 0
    actioned = 0
    would_have_actioned = 0

    try:
        with db_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM emails WHERE folder = ? AND account_id = ?",
                (config.IMAP_FOLDER, config.ACCOUNT_ID)
            ).fetchall()
    except sqlite3.Error as e:
        logger.error("Rule run: database error for rule '%s': %s", rule["name"], e)
        return redirect(url_for("rules_list"))

    if not rows:
        logger.debug("Rule run: no emails in database for folder %s", config.IMAP_FOLDER)
        flash("Rule '%s' ran: no emails in database." % rule["name"], "success")
        return redirect(url_for("rules_list"))

    logger.debug("Rule run: evaluating %s email(s) against rule '%s'", len(rows), rule["name"])

    rule_actions = rule["actions"]
    resolved_actions = [a for a in rule_actions if a["type"] not in TERMINAL_ACTIONS] + \
                       [a for a in rule_actions if a["type"] in TERMINAL_ACTIONS]

    needs_rfc822 = any(a["type"] in {"learn_spam", "learn_ham"} for a in resolved_actions)

    try:
        client = imap.connect()
        try:
            client.select_folder(config.IMAP_FOLDER)
            current_uids = set(client.search(["ALL"]))
            logger.debug("Rule run: %s UID(s) currently in %s", len(current_uids), config.IMAP_FOLDER)
            processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            for email_row in rows:
                uid = int(email_row["uid"])
                email_id = email_row["id"]

                if uid not in current_uids:
                    logger.debug("Rule run: UID %s not in current folder, skipping", uid)
                    continue

                email_data = {
                    "sender": email_row["sender"] or "",
                    "subject": email_row["subject"] or "",
                    "recipients": [r for r in (email_row["recipients"] or "").split(",") if r],
                    "raw_headers": email_row["raw_headers"] or "",
                    "attachments": json.loads(email_row["attachments"] or "[]"),
                }

                if not check_rule(rule, email_data, spam_score=email_row["spam_score"], email_id=email_id):
                    logger.debug("Rule run: UID %s did not match rule '%s'", uid, rule["name"])
                    continue

                matched += 1
                logger.debug(
                    "Rule run: UID %s (email_id=%s) matched rule '%s'",
                    uid, email_id, rule["name"],
                    extra={"email_id": email_id}
                )

                raw_message = b""
                if needs_rfc822 and not config.DRYRUN:
                    logger.debug(
                        "Rule run: fetching RFC822 for UID %s for learning",
                        uid, extra={"email_id": email_id}
                    )
                    try:
                        fetch_result = client.fetch([uid], ["RFC822"])
                        raw_message = fetch_result.get(uid, {}).get(b"RFC822", b"")
                        if not raw_message:
                            logger.warning(
                                "Rule run: RFC822 body empty for UID %s",
                                uid, extra={"email_id": email_id}
                            )
                    except Exception as e:
                        logger.error(
                            "Rule run: failed to fetch RFC822 for UID %s: %s",
                            uid, e, extra={"email_id": email_id}
                        )

                executed = []
                rspamd_learned = None
                for action in resolved_actions:
                    action_type = action["type"]
                    logger.debug(
                        "Rule run: executing action %s for UID %s",
                        action_type, uid, extra={"email_id": email_id}
                    )

                    if action_type in {"learn_spam", "learn_ham"}:
                        if not config.DRYRUN:
                            if raw_message:
                                try:
                                    if action_type == "learn_spam":
                                        ok = spam.learn_spam(raw_message, email_id=email_id)
                                    else:
                                        ok = spam.learn_ham(raw_message, email_id=email_id)
                                    if ok:
                                        rspamd_learned = "spam" if action_type == "learn_spam" else "ham"
                                        actioned += 1
                                        executed.append(action)
                                    else:
                                        logger.warning(
                                            "Rule run: %s returned failure for UID %s",
                                            action_type, uid, extra={"email_id": email_id}
                                        )
                                except Exception as e:
                                    logger.error(
                                        "Rule run: %s failed for UID %s: %s",
                                        action_type, uid, e, extra={"email_id": email_id}
                                    )
                            else:
                                logger.warning(
                                    "Rule run: skipping %s for UID %s: no RFC822 body",
                                    action_type, uid, extra={"email_id": email_id}
                                )
                        else:
                            executed.append(action)
                        continue

                    try:
                        imap.execute_action(client, action, uid, email_id=email_id)
                        executed.append(action)
                        if not config.DRYRUN:
                            actioned += 1
                    except Exception as e:
                        logger.error(
                            "Rule run: failed action %s on UID %s: %s",
                            action_type, uid, e, extra={"email_id": email_id}
                        )
                    if action_type in TERMINAL_ACTIONS:
                        break

                if config.DRYRUN:
                    would_have_actioned += len(executed)

                prefix = "[DRY RUN] " if config.DRYRUN else ""
                notes_parts = ["%sRule '%s' applied manually." % (prefix, rule["name"])]
                for a in executed:
                    notes_parts.append(action_sentence(a, config.DRYRUN))

                history = json.loads(email_row["history"] or "[]")
                new_entries = []
                if not config.DRYRUN:
                    new_entries = [
                        dict({"at": processed_at, "by": "boxwatchr", "action": a["type"]},
                             **{"destination": a["destination"]} if "destination" in a else {})
                        for a in executed
                        if a["type"] not in {"learn_spam", "learn_ham"}
                    ]
                enqueue_email_update(
                    email_id,
                    json.dumps(rule),
                    executed,
                    processed=0 if config.DRYRUN else 1,
                    processed_at=processed_at,
                    processed_notes=" ".join(notes_parts),
                    history=history + new_entries,
                    rspamd_learned=rspamd_learned,
                )
                logger.debug(
                    "Rule run: enqueued update for email_id=%s (actions=%s)",
                    email_id, [a["type"] for a in executed],
                    extra={"email_id": email_id}
                )

        finally:
            client.logout()
    except Exception as e:
        logger.error("Rule run: IMAP error for rule '%s': %s", rule["name"], e)

    if config.DRYRUN:
        logger.info("Rule '%s' run manually (DRYRUN): %s matched, %s action(s) would have been taken", rule["name"], matched, would_have_actioned)
        flash("[DRY RUN] Rule '%s' ran: %s email(s) matched, %s action(s) would have been taken." % (rule["name"], matched, would_have_actioned), "success")
    else:
        logger.info("Rule '%s' run manually: %s matched, %s action(s) taken", rule["name"], matched, actioned)
        flash("Rule '%s' ran: %s email(s) matched, %s action(s) taken." % (rule["name"], matched, actioned), "success")
    return redirect(url_for("rules_list"))
