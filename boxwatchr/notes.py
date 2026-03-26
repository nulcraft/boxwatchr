def action_sentence(action, dry_run):
    t = action["type"]
    dest = action.get("destination", "")
    if dry_run:
        if t == "move":
            return "Would have moved to %s." % dest
        if t == "mark_read":
            return "Would have marked as read."
        if t == "mark_unread":
            return "Would have marked as unread."
        if t == "flag":
            return "Would have flagged."
        if t == "unflag":
            return "Would have unflagged."
        if t == "learn_spam":
            return "Would have submitted to rspamd as spam."
        if t == "learn_ham":
            return "Would have submitted to rspamd as ham."
    else:
        if t == "move":
            return "Moved to %s." % dest
        if t == "mark_read":
            return "Marked as read."
        if t == "mark_unread":
            return "Marked as unread."
        if t == "flag":
            return "Flagged."
        if t == "unflag":
            return "Unflagged."
        if t == "learn_spam":
            return "Submitted to rspamd as spam."
        if t == "learn_ham":
            return "Submitted to rspamd as ham."
    return ""

def failed_action_sentence(action):
    t = action["type"]
    dest = action.get("destination", "")
    if t == "move":
        return "Failed to move to %s." % dest
    if t == "mark_read":
        return "Failed to mark as read."
    if t == "mark_unread":
        return "Failed to mark as unread."
    if t == "flag":
        return "Failed to flag."
    if t == "unflag":
        return "Failed to unflag."
    if t == "learn_spam":
        return "Failed to submit to rspamd as spam."
    if t == "learn_ham":
        return "Failed to submit to rspamd as ham."
    return "Action failed."

def skipped_learn_sentence(action):
    t = action["type"]
    if t == "learn_spam":
        return "Skipped rspamd spam learning: raw message not available."
    if t == "learn_ham":
        return "Skipped rspamd ham learning: raw message not available."
    return ""

def build_notes_opener(matched_rule, dry_run):
    prefix = "[DRY RUN] " if dry_run else ""
    if matched_rule:
        return "%sThe rule '%s' matched." % (prefix, matched_rule["name"])
    return "%sNo rule matched." % prefix
