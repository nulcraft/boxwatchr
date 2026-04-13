import json
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.presets")

BUILT_IN_PRESETS = [
    {
        "id": "crypto-investment-scams",
        "name": "Crypto & Investment Scams",
        "description": "Common cryptocurrency, trading, and investment fraud patterns",
        "category": "spam",
        "patterns": [
            {"field": "subject", "operator": "contains", "value": "bitcoin profit"},
            {"field": "subject", "operator": "contains", "value": "crypto investment"},
            {"field": "subject", "operator": "contains", "value": "trading signal"},
            {"field": "subject", "operator": "contains", "value": "guaranteed returns"},
            {"field": "subject", "operator": "contains", "value": "double your bitcoin"},
            {"field": "subject", "operator": "contains", "value": "crypto opportunity"},
            {"field": "subject", "operator": "contains", "value": "investment opportunity"},
            {"field": "subject", "operator": "contains", "value": "forex trading"},
            {"field": "subject", "operator": "contains", "value": "binary options"},
            {"field": "subject", "operator": "contains", "value": "passive income daily"},
        ],
        "default_actions": [
            {"type": "learn_spam"},
            {"type": "move", "destination": "Junk"},
        ],
    },
    {
        "id": "pharmacy-health-spam",
        "name": "Pharmacy & Health Spam",
        "description": "Unsolicited pharmaceutical and health product advertising",
        "category": "spam",
        "patterns": [
            {"field": "subject", "operator": "contains", "value": "online pharmacy"},
            {"field": "subject", "operator": "contains", "value": "buy viagra"},
            {"field": "subject", "operator": "contains", "value": "cheap medication"},
            {"field": "subject", "operator": "contains", "value": "weight loss pill"},
            {"field": "subject", "operator": "contains", "value": "diet supplement"},
            {"field": "subject", "operator": "contains", "value": "miracle cure"},
            {"field": "subject", "operator": "contains", "value": "canadian pharmacy"},
            {"field": "subject", "operator": "contains", "value": "prescription without"},
            {"field": "subject", "operator": "contains", "value": "discount medication"},
        ],
        "default_actions": [
            {"type": "learn_spam"},
            {"type": "move", "destination": "Junk"},
        ],
    },
    {
        "id": "lottery-prize-scams",
        "name": "Lottery & Prize Scams",
        "description": "Fake lottery winnings, prize claims, and sweepstakes fraud",
        "category": "spam",
        "patterns": [
            {"field": "subject", "operator": "contains", "value": "you have won"},
            {"field": "subject", "operator": "contains", "value": "lottery winner"},
            {"field": "subject", "operator": "contains", "value": "claim your prize"},
            {"field": "subject", "operator": "contains", "value": "congratulations winner"},
            {"field": "subject", "operator": "contains", "value": "sweepstakes notification"},
            {"field": "subject", "operator": "contains", "value": "winning notification"},
            {"field": "subject", "operator": "contains", "value": "unclaimed reward"},
            {"field": "subject", "operator": "contains", "value": "lucky draw"},
        ],
        "default_actions": [
            {"type": "learn_spam"},
            {"type": "move", "destination": "Junk"},
        ],
    },
    {
        "id": "phishing-account-alerts",
        "name": "Phishing & Fake Account Alerts",
        "description": "Fake account verification, suspension, and security alert patterns",
        "category": "spam",
        "patterns": [
            {"field": "subject", "operator": "contains", "value": "verify your account"},
            {"field": "subject", "operator": "contains", "value": "account suspended"},
            {"field": "subject", "operator": "contains", "value": "confirm your identity"},
            {"field": "subject", "operator": "contains", "value": "unusual sign-in"},
            {"field": "subject", "operator": "contains", "value": "unauthorized login"},
            {"field": "subject", "operator": "contains", "value": "security alert action required"},
            {"field": "subject", "operator": "contains", "value": "your account will be closed"},
            {"field": "subject", "operator": "contains", "value": "password expires today"},
            {"field": "subject", "operator": "contains", "value": "update your payment"},
        ],
        "default_actions": [
            {"type": "learn_spam"},
            {"type": "move", "destination": "Junk"},
        ],
    },
    {
        "id": "advance-fee-fraud",
        "name": "Advance-Fee & 419 Fraud",
        "description": "Nigerian prince, inheritance, and advance-fee scam patterns",
        "category": "spam",
        "patterns": [
            {"field": "subject", "operator": "contains", "value": "beneficiary"},
            {"field": "subject", "operator": "contains", "value": "million dollars"},
            {"field": "subject", "operator": "contains", "value": "from the desk of"},
            {"field": "subject", "operator": "contains", "value": "inheritance fund"},
            {"field": "subject", "operator": "contains", "value": "next of kin"},
            {"field": "subject", "operator": "contains", "value": "unclaimed funds"},
            {"field": "subject", "operator": "contains", "value": "confidential business proposal"},
            {"field": "subject", "operator": "contains", "value": "urgent assistance"},
        ],
        "default_actions": [
            {"type": "learn_spam"},
            {"type": "move", "destination": "Junk"},
        ],
    },
    {
        "id": "adult-content-spam",
        "name": "Adult Content",
        "description": "Unsolicited adult content and dating spam",
        "category": "spam",
        "patterns": [
            {"field": "subject", "operator": "contains", "value": "adult dating"},
            {"field": "subject", "operator": "contains", "value": "singles in your area"},
            {"field": "subject", "operator": "contains", "value": "hookup tonight"},
            {"field": "subject", "operator": "contains", "value": "hot singles"},
            {"field": "subject", "operator": "contains", "value": "lonely housewives"},
            {"field": "subject", "operator": "contains", "value": "discreet affair"},
        ],
        "default_actions": [
            {"type": "learn_spam"},
            {"type": "move", "destination": "Junk"},
        ],
    },
]


def seed_presets(conn):
    """Insert built-in presets into the database. Safe to call multiple times."""
    for preset in BUILT_IN_PRESETS:
        conn.execute("""
            INSERT INTO presets (id, name, description, category, patterns, default_actions, built_in, version)
            VALUES (?, ?, ?, ?, ?, ?, 1, 1)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                patterns = excluded.patterns,
                default_actions = excluded.default_actions,
                version = excluded.version
        """, (
            preset["id"],
            preset["name"],
            preset["description"],
            preset["category"],
            json.dumps(preset["patterns"]),
            json.dumps(preset["default_actions"]),
        ))
    logger.info("Seeded %s built-in preset pack(s)", len(BUILT_IN_PRESETS))


def expand_preset_to_rule(preset, actions_override=None):
    """Convert a preset into a rule dict suitable for insert_rule."""
    actions = actions_override if actions_override else preset["default_actions"]
    if isinstance(actions, str):
        actions = json.loads(actions)
    patterns = preset["patterns"]
    if isinstance(patterns, str):
        patterns = json.loads(patterns)
    return {
        "name": "[Preset] %s" % preset["name"],
        "match": "any",
        "conditions": patterns,
        "actions": actions,
    }


def sync_preset_rules(account_id, preset_id, enabled, actions_override=None):
    """Sync rules generated from a preset for an account.

    If enabled, ensures a rule exists for the preset. If disabled, removes it.
    """
    from boxwatchr.database import delete_preset_rules_for_account, insert_rule, get_preset

    if not enabled:
        delete_preset_rules_for_account(account_id, preset_id)
        return

    preset_row = get_preset(preset_id)
    if not preset_row:
        return

    preset = dict(preset_row)
    if isinstance(preset.get("patterns"), str):
        preset["patterns"] = json.loads(preset["patterns"])
    if isinstance(preset.get("default_actions"), str):
        preset["default_actions"] = json.loads(preset["default_actions"])

    override = None
    if actions_override:
        override = json.loads(actions_override) if isinstance(actions_override, str) else actions_override

    rule_data = expand_preset_to_rule(preset, actions_override=override)

    delete_preset_rules_for_account(account_id, preset_id)
    insert_rule(
        account_id=account_id,
        name=rule_data["name"],
        match=rule_data["match"],
        conditions_json=json.dumps(rule_data["conditions"]),
        actions_json=json.dumps(rule_data["actions"]),
        preset_id=preset_id,
    )
