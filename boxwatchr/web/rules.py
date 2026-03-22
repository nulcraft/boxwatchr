import os
import yaml
from flask import render_template, request, redirect, url_for, abort
from boxwatchr import config
from boxwatchr.web.app import app, _require_auth, _require_csrf, logger

_FIELD_LABELS = {
    "sender": "Full address",
    "sender_local": "Local part (before @)",
    "sender_domain": "Full domain",
    "sender_domain_name": "Domain name",
    "sender_domain_root": "Domain root",
    "sender_domain_tld": "TLD",
    "recipient": "Full address",
    "recipient_local": "Local part (before @)",
    "recipient_domain": "Full domain",
    "recipient_domain_name": "Domain name",
    "recipient_domain_root": "Domain root",
    "recipient_domain_tld": "TLD",
    "subject": "Subject",
    "raw_headers": "Raw headers",
    "attachment_name": "File name",
    "attachment_extension": "Extension",
    "attachment_content_type": "Content type",
    "rspamd_score": "Spam score",
}

_ACTION_LABELS = {
    "move": "Move to folder",
    "delete": "Delete (move to trash)",
    "spam": "Mark as spam",
    "mark_read": "Mark as read",
    "mark_unread": "Mark as unread",
    "flag": "Flag message",
    "unflag": "Remove flag",
    "learn_spam": "Submit to rspamd as spam",
    "learn_ham": "Submit to rspamd as ham",
}

def _read_rules_raw():
    if not os.path.exists(config.RULES_PATH):
        return []
    try:
        with open(config.RULES_PATH, "r") as f:
            data = yaml.safe_load(f)
        if not data or "rules" not in data:
            return []
        return data["rules"] or []
    except (OSError, yaml.YAMLError) as e:
        logger.error("Failed to read rules file: %s", e)
        return []

class _IndentedDumper(yaml.Dumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow=flow, indentless=False)

def _write_rules_raw(raw_rules):
    os.makedirs(os.path.dirname(config.RULES_PATH), exist_ok=True)
    with open(config.RULES_PATH, "w") as f:
        yaml.dump({"rules": raw_rules}, f, Dumper=_IndentedDumper,
                  default_flow_style=False, allow_unicode=True, sort_keys=False)

@app.route("/rules")
@_require_auth
def rules_list():
    raw_rules = _read_rules_raw()
    return render_template(
        "rules.html",
        rules=raw_rules,
        field_labels=_FIELD_LABELS,
        action_labels=_ACTION_LABELS,
        show_logout=bool(config.WEB_PASSWORD),
        run_result=request.args.get("run_result"),
    )

@app.route("/rules/<int:index>/delete", methods=["POST"])
@_require_auth
@_require_csrf
def rule_delete(index):
    raw_rules = _read_rules_raw()
    if index < 0 or index >= len(raw_rules):
        abort(404)
    deleted_name = raw_rules[index].get("name", "unknown")
    raw_rules.pop(index)
    try:
        _write_rules_raw(raw_rules)
        logger.info("User deleted rule '%s'", deleted_name)
    except OSError as e:
        logger.error("Failed to write rules file after delete: %s", e)
    return redirect(url_for("rules_list"))

@app.route("/rules/<int:index>/move-up", methods=["POST"])
@_require_auth
@_require_csrf
def rule_move_up(index):
    raw_rules = _read_rules_raw()
    if index <= 0 or index >= len(raw_rules):
        abort(404)
    raw_rules[index - 1], raw_rules[index] = raw_rules[index], raw_rules[index - 1]
    try:
        _write_rules_raw(raw_rules)
    except OSError as e:
        logger.error("Failed to write rules file after move-up: %s", e)
    return redirect(url_for("rules_list"))

@app.route("/rules/<int:index>/move-down", methods=["POST"])
@_require_auth
@_require_csrf
def rule_move_down(index):
    raw_rules = _read_rules_raw()
    if index < 0 or index >= len(raw_rules) - 1:
        abort(404)
    raw_rules[index], raw_rules[index + 1] = raw_rules[index + 1], raw_rules[index]
    try:
        _write_rules_raw(raw_rules)
    except OSError as e:
        logger.error("Failed to write rules file after move-down: %s", e)
    return redirect(url_for("rules_list"))
