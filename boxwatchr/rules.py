import re
import yaml
import threading
import tldextract
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.rules")

_rules = []
_rules_lock = threading.Lock()

def load_rules(path):
    logger.info("Loading rules from %s", path)

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("Rules file not found at %s", path)
        raise
    except yaml.YAMLError as e:
        logger.error("Failed to parse rules file: %s", e)
        raise

    if not data or "rules" not in data:
        logger.warning("No rules found in %s", path)
        return []

    validated = []
    for rule in data["rules"]:
        result = _validate_rule(rule)
        if result:
            validated.append(result)

    logger.info("Loaded %s valid rule(s)", len(validated))

    with _rules_lock:
        global _rules
        _rules = validated

    return validated

def _validate_rule(rule):
    name = rule.get("name", "unnamed")

    if "conditions" not in rule or not rule["conditions"]:
        logger.warning("Rule '%s' has no conditions and will be skipped", name)
        return None

    if "actions" not in rule or not rule["actions"]:
        logger.warning("Rule '%s' has no actions and will be skipped", name)
        return None

    match = rule.get("match", "all").lower()
    if match not in ("all", "any"):
        logger.warning("Rule '%s' has invalid match value '%s', defaulting to 'all'", name, match)
        match = "all"

    valid_fields = {
        "sender", "sender_local", "sender_domain", "sender_domain_name",
        "sender_domain_root", "sender_domain_tld",
        "recipient", "recipient_local", "recipient_domain", "recipient_domain_name",
        "recipient_domain_root", "recipient_domain_tld",
        "subject", "raw_headers"
    }

    valid_operators = {"equals", "contains", "regex"}
    valid_actions = {"move", "delete", "junk"}

    validated_conditions = []
    for condition in rule["conditions"]:
        field = condition.get("field", "")
        operator = condition.get("operator", "")
        value = condition.get("value", "")

        if field not in valid_fields:
            logger.warning("Rule '%s' has unknown field '%s' and will be skipped", name, field)
            return None

        if operator not in valid_operators:
            logger.warning("Rule '%s' has unknown operator '%s' and will be skipped", name, operator)
            return None

        if not value:
            logger.warning("Rule '%s' has an empty value and will be skipped", name)
            return None

        if operator == "regex":
            try:
                re.compile(value)
            except re.error as e:
                logger.warning("Rule '%s' has an invalid regex '%s': %s and will be skipped", name, value, e)
                return None

        validated_conditions.append({
            "field": field,
            "operator": operator,
            "value": value
        })

    validated_actions = []
    for action in rule["actions"]:
        action_type = action.get("type", "")

        if action_type not in valid_actions:
            logger.warning("Rule '%s' has unknown action '%s' and will be skipped", name, action_type)
            return None

        if action_type == "move" and not action.get("destination"):
            logger.warning("Rule '%s' has a move action but no destination and will be skipped", name)
            return None

        validated_actions.append(action)

    logger.debug("Rule '%s' validated successfully", name)

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
        # Break an email address into its parts. We use tldextract for
        # the domain_root field so that email.nfl.com correctly gives
        # "nfl" instead of "emailnfl" like a naive dot split would.
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
        parts = domain.rsplit(".", 1)
        domain_name = parts[0] if len(parts) == 2 else domain
        tld = parts[1] if len(parts) == 2 else ""

        extracted = tldextract.extract(domain)
        domain_root = extracted.domain

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

    sender_parts = split_address(sender)
    recipient_parts = [split_address(r) for r in recipients]

    return {
        "sender": sender_parts["full"],
        "sender_local": sender_parts["local"],
        "sender_domain": sender_parts["domain"],
        "sender_domain_name": sender_parts["domain_name"],
        "sender_domain_root": sender_parts["domain_root"],
        "sender_domain_tld": sender_parts["tld"],
        "recipients": recipient_parts,
        "subject": subject.lower(),
        "raw_headers": raw_headers.lower()
    }

def _match_condition(condition, fields, rule_name):
    field = condition["field"]
    operator = condition["operator"]
    value = condition["value"].lower()

    if field.startswith("recipient"):
        recipient_key = {
            "recipient": "full",
            "recipient_local": "local",
            "recipient_domain": "domain",
            "recipient_domain_name": "domain_name",
            "recipient_domain_root": "domain_root",
            "recipient_domain_tld": "tld"
        }.get(field)

        return any(
            _apply_operator(operator, r.get(recipient_key, ""), value, field, rule_name)
            for r in fields["recipients"]
        )

    field_value = fields.get(field, "")
    return _apply_operator(operator, field_value, value, field, rule_name)

def _normalize(value):
    return re.sub(r'[^a-z0-9]', '', value.lower())

def _apply_operator(operator, field_value, value, field_name, rule_name):
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
                    "Rule '%s' matched because '%s' normalized to '%s' matches '%s'",
                    rule_name, field_value, normalized_field, value
                )
            return result

        if operator == "contains":
            result = normalized_value in normalized_field
            if result and normalized_field != field_value.lower():
                logger.debug(
                    "Rule '%s' matched because '%s' normalized to '%s' contains '%s'",
                    rule_name, field_value, normalized_field, value
                )
            return result

        if operator == "regex":
            return bool(re.search(normalized_value, normalized_field))

    if operator == "equals":
        return field_value == value
    if operator == "contains":
        return value in field_value
    if operator == "regex":
        return bool(re.search(value, field_value))

    return False

def evaluate(email):
    fields = _extract_fields(email)

    with _rules_lock:
        rules = list(_rules)

    for rule in rules:
        conditions = rule["conditions"]
        match_type = rule["match"]

        results = [_match_condition(c, fields, rule["name"]) for c in conditions]

        if match_type == "all" and all(results):
            logger.info("Email matched rule '%s'", rule["name"])
            return rule

        if match_type == "any" and any(results):
            logger.info("Email matched rule '%s'", rule["name"])
            return rule

    logger.debug("Email did not match any rules")
    return None

class _RulesFileHandler(FileSystemEventHandler):
    def __init__(self, path):
        self.path = path

    def on_modified(self, event):
        if event.src_path.endswith(self.path):
            logger.info("Rules file changed, reloading")
            try:
                load_rules(self.path)
            except Exception as e:
                logger.error("Failed to reload rules: %s", e)

def watch_rules(path):
    logger.info("Watching rules file for changes: %s", path)
    event_handler = _RulesFileHandler(path)
    observer = Observer()
    observer.schedule(event_handler, path=".", recursive=False)
    observer.start()
    return observer