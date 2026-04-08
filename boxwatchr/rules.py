import re
import json
import threading
import tldextract
from datetime import datetime, timezone

_tldextract = tldextract.TLDExtract(cache_dir="/app/data/tldextract")
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.rules")

_rules = []
_rules_lock = threading.Lock()

TERMINAL_ACTIONS = {"move"}

_TEXT_OPERATORS = {"equals", "not_equals", "contains", "not_contains", "is_empty", "matches_regex"}
_NUMERIC_OPERATORS = {"greater_than", "less_than", "greater_than_or_equal", "less_than_or_equal"}
_NUMERIC_FIELDS = {"rspamd_score", "email_age_days", "email_age_hours"}
_TIME_FIELDS = {"email_age_days", "email_age_hours"}

def load_rules(account_id=None):
    global _rules

    from boxwatchr import database
    if account_id is None:
        from boxwatchr import config
        account_id = config.ACCOUNT_ID

    rows = database.get_rules(account_id)

    validated = []
    for row in rows:
        enabled = bool(row["enabled"]) if "enabled" in row.keys() else True
        rule_dict = {
            "name": row["name"],
            "match": row["match"],
            "conditions": json.loads(row["conditions"] or "[]"),
            "actions": json.loads(row["actions"] or "[]"),
            "condition_groups": json.loads(row["condition_groups"] or "[]") if "condition_groups" in row.keys() else [],
            "enabled": enabled,
        }
        if not enabled:
            logger.debug("Rule '%s' is disabled, skipping", rule_dict["name"])
            continue
        result = validate_rule(rule_dict)
        if result:
            result["id"] = row["id"]
            validated.append(result)

    with _rules_lock:
        _rules = validated

    return validated

def validate_rule(rule):
    name = rule.get("name", "").strip()
    if not name:
        logger.warning("A rule is missing a name and will be skipped")
        return None

    has_conditions = bool(rule.get("conditions"))
    has_groups = bool(rule.get("condition_groups"))
    if not has_conditions and not has_groups:
        logger.warning("Rule '%s' has no conditions and will be skipped", name)
        return None

    if "actions" not in rule or not rule["actions"]:
        logger.warning("Rule '%s' has no actions and will be skipped", name)
        return None

    match = rule.get("match", "all").lower().strip()
    if match not in ("all", "any"):
        logger.warning("Rule '%s' has invalid match value '%s', defaulting to 'all'", name, match)
        match = "all"

    valid_fields = {
        "sender", "sender_local", "sender_domain", "sender_domain_name",
        "sender_domain_root", "sender_domain_tld",
        "recipient", "recipient_local", "recipient_domain", "recipient_domain_name",
        "recipient_domain_root", "recipient_domain_tld",
        "subject", "raw_headers",
        "attachment_name", "attachment_extension", "attachment_content_type",
        "rspamd_score", "email_age_days", "email_age_hours",
    }

    valid_actions = {"move", "mark_read", "mark_unread", "flag", "unflag", "learn_spam", "learn_ham", "notify_discord", "add_label"}
    contradictory_pairs = [{"mark_read", "mark_unread"}, {"flag", "unflag"}, {"learn_spam", "learn_ham"}]

    def _validate_condition(condition, index):
        field = condition.get("field", "").strip()
        operator = condition.get("operator", "").strip()
        value = condition.get("value", "")

        if not field:
            logger.warning("Rule '%s' condition %s is missing a field and will be skipped", name, index)
            return None

        if not operator:
            logger.warning("Rule '%s' condition %s is missing an operator and will be skipped", name, index)
            return None

        if field not in valid_fields:
            logger.warning("Rule '%s' condition %s has unknown field '%s' and will be skipped", name, index, field)
            return None

        if field in _NUMERIC_FIELDS:
            if operator not in _NUMERIC_OPERATORS:
                logger.warning(
                    "Rule '%s' condition %s: %s requires a numeric operator (got '%s') and will be skipped",
                    name, index, field, operator
                )
                return None
            try:
                float(value)
            except (ValueError, TypeError):
                logger.warning(
                    "Rule '%s' condition %s: %s value must be a number (got %r) and will be skipped",
                    name, index, field, value
                )
                return None
        else:
            if operator not in _TEXT_OPERATORS:
                logger.warning(
                    "Rule '%s' condition %s has unknown operator '%s' and will be skipped",
                    name, index, operator
                )
                return None

            if operator == "matches_regex":
                try:
                    re.compile(value)
                except re.error:
                    logger.warning(
                        "Rule '%s' condition %s has invalid regex %r and will be skipped",
                        name, index, value
                    )
                    return None
            elif operator != "is_empty" and (value == "" or value is None):
                logger.warning("Rule '%s' condition %s is missing a value and will be skipped", name, index)
                return None

            if operator == "is_empty" and str(value).lower() not in ("true", "false"):
                logger.warning(
                    "Rule '%s' condition %s uses is_empty but value must be true or false and will be skipped",
                    name, index
                )
                return None

        return {"field": field, "operator": operator, "value": str(value)}

    validated_conditions = []
    for i, condition in enumerate(rule.get("conditions", [])):
        c = _validate_condition(condition, i + 1)
        if c is None:
            return None
        validated_conditions.append(c)

    validated_groups = []
    for gi, group in enumerate(rule.get("condition_groups", [])):
        group_match = group.get("match", "all").lower().strip()
        if group_match not in ("all", "any"):
            group_match = "all"
        group_conditions = []
        for i, condition in enumerate(group.get("conditions", [])):
            c = _validate_condition(condition, "group %d cond %d" % (gi + 1, i + 1))
            if c is not None:
                group_conditions.append(c)
        if group_conditions:
            validated_groups.append({"match": group_match, "conditions": group_conditions})

    validated_actions = []
    for i, action in enumerate(rule["actions"]):
        action_type = action.get("type", "").strip()

        if not action_type:
            logger.warning("Rule '%s' action %s is missing a type and will be skipped", name, i + 1)
            continue

        if action_type not in valid_actions:
            logger.warning("Rule '%s' action %s has unknown type '%s' and will be skipped", name, i + 1, action_type)
            continue

        if action_type == "move":
            destination = action.get("destination", "").strip()
            if not destination:
                logger.warning("Rule '%s' action %s is a move but has no destination and will be skipped", name, i + 1)
                continue
            validated_actions.append({"type": "move", "destination": destination})
            continue

        if action_type == "notify_discord":
            webhook_url = action.get("webhook_url", "").strip()
            if not webhook_url:
                logger.warning("Rule '%s' action %s is notify_discord but has no webhook_url and will be skipped", name, i + 1)
                continue
            if not (webhook_url.startswith("https://discord.com/api/webhooks/") or
                    webhook_url.startswith("https://discordapp.com/api/webhooks/")):
                logger.warning("Rule '%s' action %s has an invalid Discord webhook URL and will be skipped", name, i + 1)
                continue
            validated_actions.append({"type": "notify_discord", "webhook_url": webhook_url})
            continue

        if action_type == "add_label":
            label = action.get("label", "").strip()
            if not label:
                logger.warning("Rule '%s' action %s is add_label but has no label and will be skipped", name, i + 1)
                continue
            validated_actions.append({"type": "add_label", "label": label})
            continue

        validated_actions.append({"type": action_type})

    if not validated_actions:
        logger.warning("Rule '%s' has no valid actions after validation and will be skipped", name)
        return None

    seen_types = set()
    for action in validated_actions:
        action_type = action["type"]
        if action_type in seen_types:
            logger.warning("Rule '%s' has duplicate action type '%s' and will be skipped", name, action_type)
            return None
        seen_types.add(action_type)

    terminal_count = sum(1 for a in validated_actions if a["type"] in TERMINAL_ACTIONS)
    if terminal_count > 1:
        logger.warning(
            "Rule '%s' has more than one terminal action (%s) and will be skipped",
            name, "/".join(sorted(TERMINAL_ACTIONS))
        )
        return None

    action_types = {a["type"] for a in validated_actions}
    for pair in contradictory_pairs:
        if pair.issubset(action_types):
            logger.warning(
                "Rule '%s' has contradictory actions %s and will be skipped",
                name, " and ".join(sorted(pair))
            )
            return None

    result = {
        "name": name,
        "match": match,
        "conditions": validated_conditions,
        "actions": validated_actions,
        "enabled": rule.get("enabled", True),
    }
    if validated_groups:
        result["condition_groups"] = validated_groups
    return result

def _extract_fields(email):
    def strip_display_name(address):
        address = address.strip()
        if "<" in address and ">" in address:
            start = address.index("<") + 1
            end = address.index(">")
            return address[start:end].strip()
        return address

    def split_address(address):
        address = strip_display_name(address).lower()
        if "@" not in address:
            return {
                "full": address,
                "local": address,
                "domain": "",
                "domain_name": "",
                "domain_root": "",
                "tld": ""
            }
        local, domain = address.split("@", 1)
        extracted = _tldextract(domain)
        domain_root = extracted.domain
        tld = extracted.suffix
        domain_name = "%s.%s" % (extracted.subdomain, extracted.domain) if extracted.subdomain else extracted.domain

        return {
            "full": address,
            "local": local,
            "domain": domain,
            "domain_name": domain_name,
            "domain_root": domain_root,
            "tld": tld
        }

    sender = email.get("sender", "")
    subject = email.get("subject", "")
    recipients = email.get("recipients", [])
    raw_headers = email.get("raw_headers", "")
    raw_attachments = email.get("attachments", [])
    date_received = email.get("date_received", "")

    sender_parts = split_address(sender)
    recipient_parts = [split_address(r) for r in recipients]
    attachment_parts = [
        {
            "name": a.get("name", "").lower(),
            "extension": a.get("extension", "").lower(),
            "content_type": a.get("content_type", "").lower(),
        }
        for a in raw_attachments
    ]

    email_age_days = None
    email_age_hours = None
    if date_received:
        try:
            dt = datetime.strptime(date_received, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - dt
            email_age_days = delta.total_seconds() / 86400.0
            email_age_hours = delta.total_seconds() / 3600.0
        except (ValueError, TypeError):
            pass

    return {
        "sender": sender_parts["full"],
        "sender_local": sender_parts["local"],
        "sender_domain": sender_parts["domain"],
        "sender_domain_name": sender_parts["domain_name"],
        "sender_domain_root": sender_parts["domain_root"],
        "sender_domain_tld": sender_parts["tld"],
        "recipients": recipient_parts,
        "subject": subject.lower(),
        "raw_headers": raw_headers.lower(),
        "attachments": attachment_parts,
        "email_age_days": email_age_days,
        "email_age_hours": email_age_hours,
    }

def _match_condition(condition, fields, rule_name):
    field = condition["field"]
    operator = condition["operator"]
    value = condition["value"]

    if field in _NUMERIC_FIELDS:
        val = fields.get(field)
        if val is None:
            logger.debug(
                "Rule '%s': %s condition skipped, value not available",
                rule_name, field
            )
            return False
        try:
            threshold = float(value)
            val_float = float(val)
        except (ValueError, TypeError):
            return False
        if operator == "greater_than":
            result = val_float > threshold
        elif operator == "less_than":
            result = val_float < threshold
        elif operator == "greater_than_or_equal":
            result = val_float >= threshold
        elif operator == "less_than_or_equal":
            result = val_float <= threshold
        else:
            result = False
        logger.debug(
            "Rule '%s': condition field=%s operator=%s value=%s actual=%.4f => %s",
            rule_name, field, operator, threshold, val_float, result
        )
        return result

    value = value.lower()

    if field.startswith("recipient"):
        recipient_key = {
            "recipient": "full",
            "recipient_local": "local",
            "recipient_domain": "domain",
            "recipient_domain_name": "domain_name",
            "recipient_domain_root": "domain_root",
            "recipient_domain_tld": "tld"
        }.get(field)

        if not fields["recipients"]:
            result = _apply_operator(operator, "", value, field, rule_name)
            logger.debug("Rule '%s': condition field=%s operator=%s value=%r => %s (no recipients)", rule_name, field, operator, value, result)
            return result

        result = any(
            _apply_operator(operator, r.get(recipient_key, ""), value, field, rule_name)
            for r in fields["recipients"]
        )
        logger.debug("Rule '%s': condition field=%s operator=%s value=%r => %s (checked %s recipient(s))", rule_name, field, operator, value, result, len(fields["recipients"]))
        return result

    if field.startswith("attachment"):
        attachment_key = {
            "attachment_name": "name",
            "attachment_extension": "extension",
            "attachment_content_type": "content_type",
        }.get(field)

        if not fields["attachments"]:
            result = _apply_operator(operator, "", value, field, rule_name)
            logger.debug("Rule '%s': condition field=%s operator=%s value=%r => %s (no attachments)", rule_name, field, operator, value, result)
            return result

        result = any(
            _apply_operator(operator, a.get(attachment_key, ""), value, field, rule_name)
            for a in fields["attachments"]
        )
        logger.debug("Rule '%s': condition field=%s operator=%s value=%r => %s (checked %s attachment(s))", rule_name, field, operator, value, result, len(fields["attachments"]))
        return result

    field_value = fields.get(field, "")
    result = _apply_operator(operator, field_value, value, field, rule_name)
    logger.debug("Rule '%s': condition field=%s operator=%s value=%r field_value=%r => %s", rule_name, field, operator, value, field_value, result)
    return result

def _normalize(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())

def _apply_operator(operator, field_value, value, field_name, rule_name):
    if operator == "is_empty":
        is_empty = field_value == ""
        return is_empty if value == "true" else not is_empty

    if operator == "matches_regex":
        try:
            return bool(re.search(value, field_value, re.IGNORECASE))
        except re.error:
            logger.warning("Invalid regex %r in rule '%s' field %s", value, rule_name, field_name)
            return False

    normalized_fields = {
        "sender_local", "sender_domain_name", "sender_domain_root",
        "recipient_local", "recipient_domain_name", "recipient_domain_root"
    }

    if field_name in normalized_fields:
        normalized_field = _normalize(field_value)
        normalized_value = _normalize(value)

        if operator == "equals":
            result = normalized_field == normalized_value
            if result and normalized_field != field_value.lower():
                logger.debug(
                    "Rule matched because '%s' normalized to '%s' matches '%s'",
                    field_value, normalized_field, value
                )
            return result

        if operator == "not_equals":
            return normalized_field != normalized_value

        if operator == "contains":
            result = normalized_value in normalized_field
            if result and normalized_field != field_value.lower():
                logger.debug(
                    "Rule matched because '%s' normalized to '%s' contains '%s'",
                    field_value, normalized_field, value
                )
            return result

        if operator == "not_contains":
            return normalized_value not in normalized_field

    if operator == "equals":
        return field_value == value
    if operator == "not_equals":
        return field_value != value
    if operator == "contains":
        return value in field_value
    if operator == "not_contains":
        return value not in field_value

    logger.warning("Unknown operator %r in rule '%s' field %s — condition will not match", operator, rule_name, field_name)
    return False

def _match_condition_group(group, fields, rule_name):
    conditions = group.get("conditions", [])
    if not conditions:
        return True
    results = [_match_condition(c, fields, rule_name) for c in conditions]
    return any(results) if group.get("match", "all") == "any" else all(results)

def _evaluate_rule(rule, fields):
    if rule.get("condition_groups"):
        group_results = [_match_condition_group(g, fields, rule["name"]) for g in rule["condition_groups"]]
        return any(group_results) if rule["match"] == "any" else all(group_results)
    conditions = rule["conditions"]
    results = [_match_condition(c, fields, rule["name"]) for c in conditions]
    return any(results) if rule["match"] == "any" else all(results)

def check_rule(rule, email_data, spam_score=None, email_id=None):
    extra = {"email_id": email_id}
    fields = _extract_fields(email_data)
    fields["rspamd_score"] = spam_score
    matched = _evaluate_rule(rule, fields)
    logger.debug(
        "check_rule '%s' (match=%s) => %s",
        rule["name"], rule["match"], matched,
        extra=extra
    )
    return matched

def evaluate(email, spam_score=None, email_id=None):
    extra = {"email_id": email_id}
    fields = _extract_fields(email)
    fields["rspamd_score"] = spam_score
    logger.debug(
        "Evaluating rules for email from %s (subject=%r)",
        fields.get("sender", "unknown"), email.get("subject", ""),
        extra=extra
    )

    with _rules_lock:
        rules = list(_rules)

    logger.debug("Checking %s rule(s)", len(rules), extra=extra)

    for rule in rules:
        matched = _evaluate_rule(rule, fields)
        if matched:
            logger.info("Email matched rule '%s' (match=%s)", rule["name"], rule["match"], extra=extra)
            return rule
        logger.debug("Rule '%s' did not match (match=%s)", rule["name"], rule["match"], extra=extra)

    logger.debug("Email did not match any rules", extra=extra)
    return None

def _time_condition_wait_seconds(cond, fields, rule_name):
    field = cond["field"]
    operator = cond["operator"]
    threshold = float(cond["value"])
    current_age = fields.get(field)

    if current_age is None:
        return None

    multiplier = 3600.0 if field == "email_age_hours" else 86400.0

    if operator == "greater_than":
        if current_age > threshold:
            return 0.0
        return (threshold - current_age) * multiplier + 1.0
    if operator == "greater_than_or_equal":
        if current_age >= threshold:
            return 0.0
        return (threshold - current_age) * multiplier
    if operator in ("less_than", "less_than_or_equal"):
        if _match_condition(cond, fields, rule_name):
            return 0.0
        return None
    return None

def get_min_retry_wait_seconds(email_data, spam_score=None, email_id=None):
    fields = _extract_fields(email_data)
    fields["rspamd_score"] = spam_score

    with _rules_lock:
        current_rules = list(_rules)

    min_wait = None

    for rule in current_rules:
        conditions = rule.get("conditions", [])
        if not conditions:
            continue

        time_conds = [c for c in conditions if c["field"] in _TIME_FIELDS]
        if not time_conds:
            continue

        match = rule.get("match", "all")
        other_conds = [c for c in conditions if c["field"] not in _TIME_FIELDS]

        if match == "all":
            if other_conds and not all(_match_condition(c, fields, rule["name"]) for c in other_conds):
                logger.debug(
                    "Rule '%s': non-time conditions fail, no time-defer possible",
                    rule["name"], extra={"email_id": email_id}
                )
                continue
            wait_per_cond = [_time_condition_wait_seconds(c, fields, rule["name"]) for c in time_conds]
            if any(w is None for w in wait_per_cond):
                continue
            rule_wait = max(wait_per_cond)
        else:
            future_waits = [
                w for c in time_conds
                for w in [_time_condition_wait_seconds(c, fields, rule["name"])]
                if w is not None
            ]
            if not future_waits:
                continue
            rule_wait = min(future_waits)

        if rule_wait > 0:
            logger.debug(
                "Rule '%s': time-deferred match possible in %.0f s",
                rule["name"], rule_wait, extra={"email_id": email_id}
            )
            if min_wait is None or rule_wait < min_wait:
                min_wait = rule_wait

    return min_wait
