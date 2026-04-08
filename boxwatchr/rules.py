import re
import json
import threading
import tldextract

_tldextract = tldextract.TLDExtract(cache_dir="/app/data/tldextract")
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.rules")

_rules = []
_rules_lock = threading.Lock()

TERMINAL_ACTIONS = {"move"}

_TEXT_OPERATORS = {"equals", "not_equals", "contains", "not_contains", "is_empty"}
_NUMERIC_OPERATORS = {"greater_than", "less_than", "greater_than_or_equal", "less_than_or_equal"}

def load_rules(account_id=None):
    global _rules

    from boxwatchr import database
    if account_id is None:
        from boxwatchr import config
        account_id = config.ACCOUNT_ID

    rows = database.get_rules(account_id)

    validated = []
    for row in rows:
        rule_dict = {
            "name": row["name"],
            "match": row["match"],
            "conditions": json.loads(row["conditions"] or "[]"),
            "actions": json.loads(row["actions"] or "[]"),
        }
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

    if "conditions" not in rule or not rule["conditions"]:
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
        "rspamd_score",
    }

    valid_actions = {"move", "mark_read", "mark_unread", "flag", "unflag", "learn_spam", "learn_ham"}
    contradictory_pairs = [{"mark_read", "mark_unread"}, {"flag", "unflag"}, {"learn_spam", "learn_ham"}]

    validated_conditions = []
    for i, condition in enumerate(rule["conditions"]):
        field = condition.get("field", "").strip()
        operator = condition.get("operator", "").strip()
        value = condition.get("value", "")

        if not field:
            logger.warning("Rule '%s' condition %s is missing a field and will be skipped", name, i + 1)
            return None

        if not operator:
            logger.warning("Rule '%s' condition %s is missing an operator and will be skipped", name, i + 1)
            return None

        if field not in valid_fields:
            logger.warning("Rule '%s' condition %s has unknown field '%s' and will be skipped", name, i + 1, field)
            return None

        if field == "rspamd_score":
            if operator not in _NUMERIC_OPERATORS:
                logger.warning(
                    "Rule '%s' condition %s: rspamd_score requires a numeric operator (got '%s') and will be skipped",
                    name, i + 1, operator
                )
                return None
            try:
                float(value)
            except (ValueError, TypeError):
                logger.warning(
                    "Rule '%s' condition %s: rspamd_score value must be a number (got %r) and will be skipped",
                    name, i + 1, value
                )
                return None
        else:
            if operator not in _TEXT_OPERATORS:
                logger.warning(
                    "Rule '%s' condition %s has unknown operator '%s' and will be skipped",
                    name, i + 1, operator
                )
                return None

            if value == "" or value is None:
                logger.warning("Rule '%s' condition %s is missing a value and will be skipped", name, i + 1)
                return None

            if operator == "is_empty" and str(value).lower() not in ("true", "false"):
                logger.warning(
                    "Rule '%s' condition %s uses is_empty but value must be true or false and will be skipped",
                    name, i + 1
                )
                return None

        validated_conditions.append({
            "field": field,
            "operator": operator,
            "value": str(value)
        })

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

    return {
        "name": name,
        "match": match,
        "conditions": validated_conditions,
        "actions": validated_actions
    }

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
    }

def _match_condition(condition, fields, rule_name):
    field = condition["field"]
    operator = condition["operator"]
    value = condition["value"]

    if field == "rspamd_score":
        score = fields.get("rspamd_score")
        if score is None:
            logger.debug(
                "Rule '%s': rspamd_score condition skipped, score not available",
                rule_name
            )
            return False
        try:
            threshold = float(value)
            score_float = float(score)
        except (ValueError, TypeError):
            return False
        if operator == "greater_than":
            result = score_float > threshold
        elif operator == "less_than":
            result = score_float < threshold
        elif operator == "greater_than_or_equal":
            result = score_float >= threshold
        elif operator == "less_than_or_equal":
            result = score_float <= threshold
        else:
            result = False
        logger.debug(
            "Rule '%s': condition field=rspamd_score operator=%s value=%s score=%.2f => %s",
            rule_name, operator, threshold, score_float, result
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

def check_rule(rule, email_data, spam_score=None, email_id=None):
    extra = {"email_id": email_id}
    fields = _extract_fields(email_data)
    fields["rspamd_score"] = spam_score
    conditions = rule["conditions"]
    results = [_match_condition(c, fields, rule["name"]) for c in conditions]
    logger.debug(
        "check_rule '%s' (match=%s): condition results=%s => %s",
        rule["name"], rule["match"], results,
        any(results) if rule["match"] == "any" else all(results),
        extra=extra
    )
    if rule["match"] == "any":
        return any(results)
    return all(results)

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
        conditions = rule["conditions"]
        match_type = rule["match"]

        results = [_match_condition(c, fields, rule["name"]) for c in conditions]

        if match_type == "all" and all(results):
            logger.info("Email matched rule '%s' (match=all, %s/%s conditions met)", rule["name"], sum(results), len(results), extra=extra)
            return rule

        if match_type == "any" and any(results):
            logger.info("Email matched rule '%s' (match=any, %s/%s conditions met)", rule["name"], sum(results), len(results), extra=extra)
            return rule

        logger.debug("Rule '%s' did not match (match=%s, results=%s)", rule["name"], match_type, results, extra=extra)

    logger.debug("Email did not match any rules", extra=extra)
    return None
