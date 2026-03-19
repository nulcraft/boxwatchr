import sys
import os
import signal
import time
import uuid
import json
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email import message_from_bytes, message_from_string
from boxwatchr import config, imap, spam, rules, health
from boxwatchr.web.dashboard import start_dashboard
from boxwatchr.imap import FatalImapError
from boxwatchr.database import initialize, start_flusher, verify, set_processing, clear_email_id_from_logs, enqueue_email, enqueue_email_update, flush, get_known_uids, get_unprocessed_emails, get_email_by_message_id, update_email_uid, relink_logs_to_email
from boxwatchr.rules import load_rules, watch_rules, TERMINAL_ACTIONS
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.main")

def _fatal_exit(message):
    logger.error("Fatal error: %s. Shutting down.", message)
    flush()
    os.kill(1, signal.SIGTERM)
    sys.exit(2)

def _decode(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value

def _parse_attachments(raw_message):
    if not raw_message:
        return []
    attachments = []
    try:
        if isinstance(raw_message, bytes):
            msg = message_from_bytes(raw_message)
        else:
            msg = message_from_string(raw_message)
        for part in msg.walk():
            if part.get_content_disposition() == "inline":
                continue
            filename = part.get_filename()
            if not filename:
                continue
            content_type = part.get_content_type() or ""
            if ";" in content_type:
                content_type = content_type.split(";")[0].strip()
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            attachments.append({
                "name": filename,
                "extension": ext,
                "content_type": content_type.lower(),
            })
    except Exception as e:
        logger.warning("Could not parse attachments: %s", e)
    return attachments

def _build_notes_opener(matched_rule, spam_score, dry_run):
    prefix = "[DRY RUN] " if dry_run else ""
    if matched_rule:
        return "%sThe rule '%s' matched." % (prefix, matched_rule["name"])
    if spam_score is not None and spam_score >= config.SPAM_THRESHOLD:
        return "%sNo rule matched. Spam score %.1f exceeded threshold." % (prefix, spam_score)
    return "%sNo rule matched." % prefix

def _action_sentence(action, dry_run):
    t = action["type"]
    dest = action.get("destination", "")
    if dry_run:
        if t == "move":
            return "Would have moved to %s." % dest
        if t == "delete":
            return "Would have moved to trash."
        if t == "junk":
            return "Would have moved to spam."
        if t == "mark_read":
            return "Would have marked as read."
        if t == "mark_unread":
            return "Would have marked as unread."
    else:
        if t == "move":
            return "Moved to %s." % dest
        if t == "delete":
            return "Moved to trash."
        if t == "junk":
            return "Moved to spam."
        if t == "mark_read":
            return "Marked as read."
        if t == "mark_unread":
            return "Marked as unread."
    return ""

def _failed_action_sentence(action):
    t = action["type"]
    dest = action.get("destination", "")
    if t == "move":
        return "Failed to move to %s." % dest
    if t == "delete":
        return "Failed to move to trash."
    if t == "junk":
        return "Failed to move to spam."
    if t == "mark_read":
        return "Failed to mark as read."
    if t == "mark_unread":
        return "Failed to mark as unread."
    return "Action failed."

def _learn_sentence(learn_type, dry_run):
    if dry_run:
        if learn_type == "ham":
            return "Would have submitted to rspamd as ham."
        if learn_type == "spam":
            return "Would have submitted to rspamd as spam."
    else:
        if learn_type == "ham":
            return "Submitted to rspamd as ham."
        if learn_type == "spam":
            return "Submitted to rspamd as spam."
    return ""

def _verify_rule_destinations(client, loaded_rules):
    destinations = {
        action["destination"]
        for rule in loaded_rules
        for action in rule["actions"]
        if action["type"] == "move"
    }
    if not destinations:
        return
    try:
        folder_names = imap.list_folder_names(client)
    except Exception as e:
        _fatal_exit("Could not list IMAP folders to verify rule destinations: %s" % e)
    missing = [d for d in sorted(destinations) if d not in folder_names]
    if missing:
        _fatal_exit("Rule destination folder(s) not found on server: %s" % ", ".join(missing))
    logger.info("All rule destination folders verified on server")

def _should_learn(learn_type):
    return (
        learn_type is not None
        and config.SPAM_LEARNING != "off"
        and (
            (learn_type == "spam" and config.SPAM_LEARNING in ("spam", "both"))
            or (learn_type == "ham" and config.SPAM_LEARNING in ("ham", "both"))
        )
    )

def startup_scan(client):
    logger.info("Scanning %s for untracked emails", config.IMAP_FOLDER)

    current_uids = imap.get_existing_uids(client)
    known_uids = get_known_uids(config.IMAP_FOLDER)
    untracked = current_uids - known_uids

    if not untracked:
        logger.info("No untracked emails found in %s", config.IMAP_FOLDER)
        return current_uids

    logger.info("Found %s untracked email(s) in %s, processing now", len(untracked), config.IMAP_FOLDER)

    for uid in untracked:
        try:
            message = imap.fetch_message(client, uid)
            process_email(client, uid, message)
        except Exception as e:
            logger.error("Failed to process email UID %s during startup scan: %s", uid, e)

    logger.info("Startup scan complete")
    return current_uids


def reprocess_pending_emails(client, current_uids):
    pending = get_unprocessed_emails()
    if not pending:
        logger.info("No pending emails to reprocess")
        return

    logger.info("Found %s pending email(s) to reprocess", len(pending))

    for row in pending:
        email_id = row["id"]
        uid = int(row["uid"])
        spam_score = row["spam_score"]
        processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if uid not in current_uids:
            logger.info(
                "Pending email UID %s no longer in %s, marking processed",
                uid, config.IMAP_FOLDER,
                extra={"email_id": email_id}
            )
            enqueue_email_update(
                email_id,
                row["rule_matched"],
                json.loads(row["actions"] or "[]"),
                processed=1,
                processed_at=processed_at,
                processed_notes="Email no longer in folder since last run. No action taken.",
            )
            continue

        stored_attachments = json.loads(row["attachments"] or "[]")
        email_data = {
            "sender": row["sender"] or "",
            "subject": row["subject"] or "",
            "recipients": [r for r in row["recipients"].split(",") if r] if row["recipients"] else [],
            "raw_headers": row["raw_headers"] or "",
            "attachments": stored_attachments,
        }

        matched_rule = rules.evaluate(email_data)
        rule_name = matched_rule["name"] if matched_rule else "none"
        logger.info(
            "Pending email UID %s re-evaluated: rule=%s",
            uid, rule_name, extra={"email_id": email_id}
        )

        learn_type = None
        actions = []

        if matched_rule:
            learn_type = matched_rule["learn"]
            rule_actions = matched_rule["actions"]
            non_terminal = [a for a in rule_actions if a["type"] not in TERMINAL_ACTIONS]
            terminal = [a for a in rule_actions if a["type"] in TERMINAL_ACTIONS]
            for action in non_terminal + terminal:
                action_type = action["type"]
                if action_type == "move":
                    actions.append({"type": "move", "destination": action["destination"]})
                elif action_type == "delete":
                    actions.append({"type": "delete", "destination": config.IMAP_TRASH_FOLDER})
                elif action_type == "junk":
                    actions.append({"type": "junk", "destination": config.IMAP_SPAM_FOLDER})
                elif action_type == "mark_read":
                    actions.append({"type": "mark_read"})
                elif action_type == "mark_unread":
                    actions.append({"type": "mark_unread"})

        elif spam_score is not None and spam_score >= config.SPAM_THRESHOLD:
            learn_type = "spam"
            if config.SPAM_ACTION == "delete":
                actions.append({"type": "delete", "destination": config.IMAP_TRASH_FOLDER})
            else:
                actions.append({"type": "junk", "destination": config.IMAP_SPAM_FOLDER})

        elif spam_score is not None and spam_score <= config.HAM_THRESHOLD:
            learn_type = "ham"

        all_ok = True
        executed = []
        for action in actions:
            action_type = action["type"]
            dest = action.get("destination")
            logger.info(
                "Reprocessing pending email UID %s: action=%s, destination=%s, rule=%s",
                uid, action_type, dest or "none", rule_name,
                extra={"email_id": email_id}
            )
            try:
                if action_type == "mark_read":
                    imap.mark_read(client, uid, email_id=email_id)
                elif action_type == "mark_unread":
                    imap.mark_unread(client, uid, email_id=email_id)
                elif action_type in TERMINAL_ACTIONS:
                    imap.move_message(client, uid, dest, email_id=email_id)
                executed.append((action, False))
                if action_type in TERMINAL_ACTIONS:
                    break
            except Exception as e:
                logger.error(
                    "Failed to execute action %s on pending email UID %s: %s",
                    action_type, uid, e,
                    extra={"email_id": email_id}
                )
                executed.append((action, True))
                all_ok = False
                if action_type in TERMINAL_ACTIONS:
                    break

        will_learn = _should_learn(learn_type)

        notes_parts = [_build_notes_opener(matched_rule, spam_score, config.DRYRUN)]
        if executed:
            for action, failed in executed:
                if failed:
                    notes_parts.append(_failed_action_sentence(action))
                else:
                    notes_parts.append(_action_sentence(action, config.DRYRUN))
        else:
            notes_parts.append("No action taken.")
        if will_learn:
            notes_parts.append(_learn_sentence(learn_type, config.DRYRUN))
        processed_notes = " ".join(notes_parts)

        enqueue_email_update(
            email_id,
            json.dumps(matched_rule) if matched_rule else None,
            actions,
            processed=0 if (not all_ok or config.DRYRUN) else 1,
            processed_at=processed_at,
            processed_notes=processed_notes,
        )
        logger.debug("Enqueued update for pending email %s", email_id, extra={"email_id": email_id})

    logger.info("Pending email reprocessing complete")


def process_email(client, uid, message):
    email_id = str(uuid.uuid4())
    email_enqueued = False
    set_processing(True)
    try:
        msg_data = message.get(uid, {})
        raw_message = msg_data.get(b"RFC822", b"")
        message_size = msg_data.get(b"RFC822.SIZE", 0)
        envelope = msg_data.get(b"ENVELOPE")

        sender = ""
        if envelope and envelope.from_:
            addr = envelope.from_[0]
            mailbox = _decode(addr.mailbox)
            host = _decode(addr.host)
            sender = f"{mailbox}@{host}" if host else mailbox

        subject = _decode(envelope.subject) if envelope else ""

        date_received = ""
        if envelope and envelope.date:
            date_received = envelope.date.strftime("%Y-%m-%d %H:%M:%S")

        recipients = []
        for addr_list in ([envelope.to, envelope.cc] if envelope else []):
            if addr_list:
                for addr in addr_list:
                    mailbox = _decode(addr.mailbox)
                    host = _decode(addr.host)
                    if mailbox and host:
                        recipients.append(f"{mailbox}@{host}")

        raw_text = _decode(raw_message)
        if "\r\n\r\n" in raw_text:
            raw_headers = raw_text.split("\r\n\r\n", 1)[0]
        elif "\n\n" in raw_text:
            raw_headers = raw_text.split("\n\n", 1)[0]
        else:
            raw_headers = raw_text

        message_id = ""
        try:
            msg_obj = message_from_bytes(raw_message) if isinstance(raw_message, bytes) else message_from_string(raw_message)
            message_id = (msg_obj.get("Message-ID") or "").strip()
        except Exception:
            pass

        attachments = _parse_attachments(raw_message)

        email_data = {
            "sender": sender,
            "subject": subject,
            "recipients": recipients,
            "raw_headers": raw_headers,
            "attachments": attachments,
        }

        logger.info("Processing email UID %s from %s", uid, sender, extra={"email_id": email_id})

        spam_result = spam.check(raw_message, email_id=email_id)
        if spam_result is None:
            raise RuntimeError("rspamd unreachable")

        spam_score = spam_result["score"]
        matched_rule = rules.evaluate(email_data)
        rule_name = matched_rule["name"] if matched_rule else "none"

        existing = get_email_by_message_id(message_id) if message_id else None
        user_override = existing["user_action"] if existing is not None else None
        skip_spam_action = existing is not None and user_override != "spam"

        if skip_spam_action:
            if user_override == "ham":
                logger.info(
                    "Email UID %s was previously marked not spam, skipping spam action",
                    uid, extra={"email_id": email_id}
                )
            else:
                logger.info(
                    "Email UID %s was already processed, skipping spam action",
                    uid, extra={"email_id": email_id}
                )

        learn_type = None
        actions = []

        if matched_rule:
            learn_type = matched_rule["learn"]
            rule_actions = matched_rule["actions"]
            non_terminal = [a for a in rule_actions if a["type"] not in TERMINAL_ACTIONS]
            terminal = [a for a in rule_actions if a["type"] in TERMINAL_ACTIONS]
            for action in non_terminal + terminal:
                action_type = action["type"]
                if action_type == "move":
                    actions.append({"type": "move", "destination": action["destination"]})
                elif action_type == "delete":
                    actions.append({"type": "delete", "destination": config.IMAP_TRASH_FOLDER})
                elif action_type == "junk":
                    actions.append({"type": "junk", "destination": config.IMAP_SPAM_FOLDER})
                elif action_type == "mark_read":
                    actions.append({"type": "mark_read"})
                elif action_type == "mark_unread":
                    actions.append({"type": "mark_unread"})

        elif spam_result["is_spam"] and not skip_spam_action:
            learn_type = "spam"
            if config.SPAM_ACTION == "delete":
                actions.append({"type": "delete", "destination": config.IMAP_TRASH_FOLDER})
            else:
                actions.append({"type": "junk", "destination": config.IMAP_SPAM_FOLDER})

        elif spam_result["score"] <= config.HAM_THRESHOLD:
            learn_type = "ham"

        for action in actions:
            action_type = action["type"]
            dest = action.get("destination")
            if action_type == "mark_read":
                imap.mark_read(client, uid, email_id=email_id)
            elif action_type == "mark_unread":
                imap.mark_unread(client, uid, email_id=email_id)
            elif action_type in TERMINAL_ACTIONS:
                imap.move_message(client, uid, dest, email_id=email_id)
                break

        will_learn = _should_learn(learn_type)

        if not config.DRYRUN and will_learn:
            if learn_type == "spam":
                spam.learn_spam(raw_message, email_id=email_id)
            elif learn_type == "ham":
                spam.learn_ham(raw_message, email_id=email_id)

        notes_parts = [_build_notes_opener(matched_rule, spam_score, config.DRYRUN)]
        if actions:
            for action in actions:
                notes_parts.append(_action_sentence(action, config.DRYRUN))
        else:
            notes_parts.append("No action taken.")
        if will_learn:
            notes_parts.append(_learn_sentence(learn_type, config.DRYRUN))
        processed_notes = " ".join(notes_parts)

        processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if existing is not None:
            relink_logs_to_email(email_id, existing["id"])
            update_email_uid(existing["id"], str(uid))
        else:
            enqueue_email(
                uid=str(uid),
                folder=config.IMAP_FOLDER,
                sender=sender,
                recipients=",".join(recipients),
                subject=subject,
                date_received=date_received,
                message_size=message_size,
                spam_score=spam_score,
                rule_matched=json.dumps(matched_rule) if matched_rule else None,
                actions=actions,
                raw_headers=raw_headers,
                attachments=attachments,
                processed=0 if config.DRYRUN else 1,
                processed_at=processed_at,
                processed_notes=processed_notes,
                email_id=email_id,
                message_id=message_id,
                user_action=user_override,
            )

        email_enqueued = True
        logger.info(
            "Email UID %s processed: actions=[%s], rule=%s, spam_score=%.2f",
            uid, ", ".join(a["type"] for a in actions) if actions else "none", rule_name, spam_score,
            extra={"email_id": existing["id"] if existing is not None else email_id}
        )

    except Exception as e:
        logger.error("Failed to process email UID %s: %s", uid, e)
        raise
    finally:
        if not email_enqueued:
            clear_email_id_from_logs(email_id)
        set_processing(False)

def main():
    try:
        initialize()
        start_flusher()
        verify()
    except Exception as e:
        _fatal_exit("Database initialization failed: %s" % e)
    start_dashboard()

    logger.info("boxwatchr starting up")
    logger.info("Dry run mode: %s", config.DRYRUN)
    logger.info("Log level: %s", config.LOG_LEVEL)
    logger.info("IMAP host: %s:%s, folder: %s", config.IMAP_HOST, config.IMAP_PORT, config.IMAP_FOLDER)
    logger.info("Spam threshold: %.1f, action: %s", config.SPAM_THRESHOLD, config.SPAM_ACTION)
    logger.info("Spam learning: %s, ham threshold: %.1f", config.SPAM_LEARNING, config.HAM_THRESHOLD)

    health.wait_for_services(startup=True)
    health.start_monitor()

    if not config.IMAP_TRASH_FOLDER or not config.IMAP_SPAM_FOLDER:
        _detect_client = imap.connect()
        try:
            detected_trash, detected_junk = imap.detect_special_folders(_detect_client)
        finally:
            _detect_client.logout()

        if not config.IMAP_TRASH_FOLDER:
            if detected_trash:
                config.IMAP_TRASH_FOLDER = detected_trash
                logger.info("Trash folder auto-detected: %s", config.IMAP_TRASH_FOLDER)
            else:
                _fatal_exit("Trash folder not configured and could not be detected from server. Set IMAP_TRASH_FOLDER in .env")

        if not config.IMAP_SPAM_FOLDER:
            if detected_junk:
                config.IMAP_SPAM_FOLDER = detected_junk
                logger.info("Spam folder auto-detected: %s", config.IMAP_SPAM_FOLDER)
            else:
                _fatal_exit("Spam folder not configured and could not be detected from server. Set IMAP_SPAM_FOLDER in .env")

    logger.info("Trash folder: %s, spam folder: %s", config.IMAP_TRASH_FOLDER, config.IMAP_SPAM_FOLDER)

    logger.info("Loading rules from config/rules.yaml")
    loaded_rules = []
    try:
        loaded_rules = load_rules("config/rules.yaml")
    except Exception:
        _fatal_exit("Cannot start: fix config/rules.yaml and restart")

    _dest_client = imap.connect()
    try:
        _verify_rule_destinations(_dest_client, loaded_rules)
    finally:
        _dest_client.logout()

    logger.info("Watching config/rules.yaml for changes")
    observer = watch_rules("config/rules.yaml")

    logger.info("boxwatchr is running")

    try:
        while True:
            startup_client = imap.connect()
            imap.select_folder(startup_client)
            try:
                current_uids = startup_scan(startup_client)
                reprocess_pending_emails(startup_client, current_uids)
            finally:
                startup_client.logout()

            try:
                imap.watch(process_email)
            except FatalImapError:
                raise
            except Exception as e:
                logger.error("Connection lost: %s. Waiting for services to recover...", e)
                health.wait_for_services()
                logger.info("Services recovered, reconnecting...")

    except FatalImapError as e:
        observer.stop()
        observer.join()
        _fatal_exit(str(e))
    except KeyboardInterrupt:
        logger.info("Shutting down")
        observer.stop()
        observer.join()

if __name__ == "__main__":
    main()