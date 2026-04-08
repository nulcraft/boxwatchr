import json
import sqlite3
from flask import render_template, abort, request
from boxwatchr import config, imap
from boxwatchr.database import db_connection, get_rule
from datetime import datetime, timezone
from boxwatchr.web.app import app, _require_auth, _score_class, logger


@app.route("/emails/<email_id>")
@_require_auth
def email_detail(email_id):
    try:
        with db_connection() as conn:
            row = conn.execute(
                "SELECT * FROM emails WHERE id = ?", (email_id,)
            ).fetchone()

            if row is None:
                abort(404)

            log_rows = conn.execute(
                """SELECT level, logger_name, message, logged_at
                   FROM logs
                   WHERE email_id = ?
                   ORDER BY logged_at ASC""",
                (email_id,)
            ).fetchall()
    except sqlite3.Error as e:
        logger.error("Failed to query email detail for %s: %s", email_id, e)
        raise

    actions = json.loads(row["actions"] or "[]")
    attachments = json.loads(row["attachments"] or "[]")
    history = json.loads(row["history"] or "[]")
    rule = None
    rule_current_name = None
    rule_exists = False
    if row["rule_matched"]:
        try:
            rule = json.loads(row["rule_matched"])
        except json.JSONDecodeError:
            pass
    if rule:
        rule_id = rule.get("id")
        if rule_id:
            rule_row = get_rule(rule_id)
            if rule_row:
                rule_current_name = rule_row["name"]
                rule_exists = True
        if not rule_current_name:
            rule_current_name = rule.get("name")

    rspamd_symbols = []
    raw_symbols = row["rspamd_symbols"] if "rspamd_symbols" in row.keys() else None
    if raw_symbols:
        try:
            sym_dict = json.loads(raw_symbols)
            rspamd_symbols = sorted(
                [
                    {"name": k, "score": v.get("score", 0), "description": v.get("description", "")}
                    for k, v in sym_dict.items()
                ],
                key=lambda x: abs(x["score"]),
                reverse=True
            )
        except (json.JSONDecodeError, TypeError):
            pass

    email = {
        "id": row["id"],
        "uid": row["uid"],
        "folder": row["folder"],
        "sender": row["sender"],
        "recipients": row["recipients"],
        "subject": row["subject"],
        "date_received": row["date_received"],
        "message_size": row["message_size"],
        "spam_score": row["spam_score"],
        "score_class": _score_class(row["spam_score"]),
        "rule": rule,
        "rule_current_name": rule_current_name,
        "rule_exists": rule_exists,
        "actions": actions,
        "history": history,
        "attachments": attachments,
        "raw_headers": row["raw_headers"],
        "processed": row["processed"],
        "processed_at": row["processed_at"],
        "processed_notes": row["processed_notes"],
        "body_text": row["body_text"] if "body_text" in row.keys() else None,
        "rspamd_symbols": rspamd_symbols,
    }

    folders = imap.get_folder_list() if config.SETUP_COMPLETE else []
    logs = [dict(r) for r in log_rows]

    return render_template(
        "email_detail.html",
        email=email,
        logs=logs,
        folders=folders,
        show_logout=bool(config.WEB_PASSWORD),
    )


@app.route("/emails/<email_id>/action", methods=["POST"])
@_require_auth
def email_action(email_id):
    from boxwatchr.web.app import _check_csrf
    _check_csrf()

    try:
        with db_connection() as conn:
            row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    except sqlite3.Error as e:
        logger.error("Failed to fetch email %s for action: %s", email_id, e)
        return json.dumps({"error": "Database error"}), 500, {"Content-Type": "application/json"}

    if row is None:
        return json.dumps({"error": "Email not found"}), 404, {"Content-Type": "application/json"}

    data = request.get_json(silent=True)
    if not data:
        return json.dumps({"error": "Invalid JSON"}), 400, {"Content-Type": "application/json"}

    action_type = data.get("action", "")
    uid = int(row["uid"])

    valid_actions = {"move", "mark_read", "mark_unread", "flag", "unflag", "learn_spam", "learn_ham", "add_label"}
    if action_type not in valid_actions:
        return json.dumps({"error": "Invalid action"}), 400, {"Content-Type": "application/json"}

    try:
        client = imap.connect()
        try:
            client.select_folder(row["folder"])
            action = {"type": action_type}
            if action_type == "move":
                dest = data.get("destination", "")
                if not dest:
                    return json.dumps({"error": "No destination"}), 400, {"Content-Type": "application/json"}
                action["destination"] = dest
            elif action_type == "add_label":
                label = data.get("label", "")
                if not label:
                    return json.dumps({"error": "No label"}), 400, {"Content-Type": "application/json"}
                action["label"] = label

            if action_type in ("learn_spam", "learn_ham"):
                from boxwatchr import spam as _spam
                fetch_result = client.fetch([uid], ["RFC822"])
                raw_message = fetch_result.get(uid, {}).get(b"RFC822", b"")
                if not raw_message:
                    return json.dumps({"error": "Could not fetch email body"}), 500, {"Content-Type": "application/json"}
                ok = _spam.learn_spam(raw_message, email_id=email_id) if action_type == "learn_spam" else _spam.learn_ham(raw_message, email_id=email_id)
                if not ok:
                    return json.dumps({"error": "rspamd learning failed"}), 500, {"Content-Type": "application/json"}
                learned = "spam" if action_type == "learn_spam" else "ham"
                try:
                    with db_connection() as conn2:
                        conn2.execute("UPDATE emails SET rspamd_learned = ? WHERE id = ?", (learned, email_id))
                        conn2.commit()
                except sqlite3.Error:
                    pass
            else:
                imap.execute_action(client, action, uid, email_id=email_id)

            processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            history = json.loads(row["history"] or "[]")
            entry = {"at": processed_at, "by": "user", "action": action_type}
            if action_type == "move":
                entry["destination"] = action.get("destination", "")
            if action_type == "add_label":
                entry["label"] = action.get("label", "")
            history.append(entry)
            try:
                with db_connection() as conn3:
                    conn3.execute("UPDATE emails SET history = ? WHERE id = ?", (json.dumps(history), email_id))
                    conn3.commit()
            except sqlite3.Error as e:
                logger.error("Failed to update history for email %s: %s", email_id, e)

            logger.info("User executed manual action '%s' on email %s", action_type, email_id)
            return json.dumps({"ok": True}), 200, {"Content-Type": "application/json"}
        finally:
            client.logout()
    except Exception as e:
        logger.error("Manual action '%s' failed for email %s: %s", action_type, email_id, e)
        return json.dumps({"error": "Action failed"}), 500, {"Content-Type": "application/json"}
