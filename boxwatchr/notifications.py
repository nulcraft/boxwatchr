import requests
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.notifications")

def send_discord_notification(webhook_url, email_data, rule_name, spam_score=None, email_id=None, actions=None):
    if not webhook_url:
        logger.warning("Discord notification skipped: no webhook URL", extra={"email_id": email_id})
        return False

    sender = email_data.get("sender", "")
    subject = email_data.get("subject", "")

    if spam_score is not None and spam_score >= 10:
        color = 0xFF4444
    elif spam_score is not None and spam_score >= 5:
        color = 0xFF9900
    else:
        color = 0x5865F2

    action_summary = ""
    if actions:
        parts = []
        for a in actions:
            t = a["type"]
            if t == "move":
                parts.append("Move to %s" % a.get("destination", ""))
            elif t == "mark_read":
                parts.append("Mark read")
            elif t == "mark_unread":
                parts.append("Mark unread")
            elif t == "flag":
                parts.append("Flag")
            elif t == "unflag":
                parts.append("Unflag")
            elif t == "learn_spam":
                parts.append("Learn spam")
            elif t == "learn_ham":
                parts.append("Learn ham")
            elif t == "notify_discord":
                pass
            elif t == "add_label":
                parts.append("Add label: %s" % a.get("label", ""))
        action_summary = ", ".join(parts) if parts else "None"

    embed = {
        "title": "Rule matched: %s" % rule_name,
        "color": color,
        "fields": [
            {"name": "From", "value": sender or "(unknown)", "inline": True},
            {"name": "Subject", "value": subject or "(no subject)", "inline": True},
        ],
    }

    if spam_score is not None:
        embed["fields"].append({"name": "Spam score", "value": "%.2f" % spam_score, "inline": True})

    if action_summary:
        embed["fields"].append({"name": "Actions", "value": action_summary, "inline": False})

    payload = {"embeds": [embed]}

    try:
        response = requests.post(webhook_url, json=payload, timeout=5)
        if response.status_code in (200, 204):
            logger.debug(
                "Discord notification sent for rule '%s'",
                rule_name,
                extra={"email_id": email_id}
            )
            return True
        logger.warning(
            "Discord webhook returned status %s: %s",
            response.status_code,
            response.text,
            extra={"email_id": email_id}
        )
        return False

    except requests.exceptions.Timeout:
        logger.error("Discord webhook request timed out", extra={"email_id": email_id})
        return False

    except requests.exceptions.ConnectionError as e:
        logger.error("Could not reach Discord webhook: %s", e, extra={"email_id": email_id})
        return False

    except Exception as e:
        logger.error("Unexpected error sending Discord notification: %s", e, extra={"email_id": email_id})
        return False
