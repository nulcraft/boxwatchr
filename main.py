import json
import uuid
import time
import signal as _signal
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email import message_from_bytes, message_from_string
from boxwatchr import config, imap, spam, rules, health, __version__
from boxwatchr.web.dashboard import start_dashboard
from boxwatchr.imap import FatalImapError
from boxwatchr.notes import action_sentence, failed_action_sentence, build_notes_opener
from boxwatchr.database import set_processing, clear_email_id_from_logs, enqueue_email, enqueue_email_update, get_known_uids, get_unprocessed_emails, get_email_by_message_id, update_email_uid
from boxwatchr.rules import watch_rules, TERMINAL_ACTIONS
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.main")

_BANNER = r"""
     ____ _________ ____ ____ ____ ____ ____ ____ ____ ____ ____
    ||> |||       |||b |||o |||x |||w |||a |||t |||c |||h |||r ||
    ||__|||_______|||__|||__|||__|||__|||__|||__|||__|||__|||__||
    |/__\|/_______\|/__\|/__\|/__\|/__\|/__\|/__\|/__\|/__\|/__\|
"""

_shutdown = False

def _handle_sigterm(signum, frame):
    global _shutdown
    logger.info("Received SIGTERM, shutting down gracefully")
    _shutdown = True
    imap.request_stop()

def _print_banner():
    lines = _BANNER.split("\n")
    width = max(len(line) for line in lines)
    print(_BANNER, flush=True)
    print(__version__.center(width), flush=True)
    print(flush=True)

def _print_startup_checks(loaded_rules):
    divider = "=" * 35
    print(divider, flush=True)
    print("boxwatchr checks", flush=True)
    print(divider, flush=True)
    print("RSPAMD password configured", flush=True)
    print("IMAP server: %s:%s" % (config.IMAP_HOST, config.IMAP_PORT), flush=True)
    print("Monitoring folder: %s" % config.IMAP_FOLDER, flush=True)
    print("Trash folder: %s" % config.IMAP_TRASH_FOLDER, flush=True)
    print("Spam folder: %s" % config.IMAP_SPAM_FOLDER, flush=True)
    print("Dry run: %s" % ("enabled" if config.DRYRUN else "disabled"), flush=True)
    print("Spam threshold: ≥%.1f, action: %s" % (config.SPAM_THRESHOLD, config.SPAM_ACTION), flush=True)
    print("Spam learning: %s, ham threshold: ≤%.1f" % (config.SPAM_LEARNING, config.HAM_THRESHOLD), flush=True)
    print("Rules loaded: %d" % len(loaded_rules), flush=True)
    print(flush=True)

def _fatal_exit(message):
    logger.error("Fatal error: %s", message)
    logger.error("Shutting down.")
    health.fatal_shutdown()

def _resolve_rule_actions(rule_actions, uid, email_id):
    actions = []
    non_terminal = [a for a in rule_actions if a["type"] not in TERMINAL_ACTIONS]
    terminal = [a for a in rule_actions if a["type"] in TERMINAL_ACTIONS]
    for action in non_terminal + terminal:
        action_type = action["type"]
        if action_type == "move":
            actions.append({"type": "move", "destination": action["destination"]})
        elif action_type == "delete":
            if config.IMAP_TRASH_FOLDER:
                actions.append({"type": "delete", "destination": config.IMAP_TRASH_FOLDER})
            else:
                logger.error(
                    "Cannot execute 'delete' action for UID %s: trash folder not configured. Set it in the Config page.",
                    uid, extra={"email_id": email_id}
                )
        elif action_type == "spam":
            if config.IMAP_SPAM_FOLDER:
                actions.append({"type": "spam", "destination": config.IMAP_SPAM_FOLDER})
            else:
                logger.error(
                    "Cannot execute 'spam' action for UID %s: spam folder not configured. Set it in the Config page.",
                    uid, extra={"email_id": email_id}
                )
        else:
            actions.append({"type": action_type})
    return actions

def _resolve_spam_action(uid, email_id):
    if config.SPAM_ACTION == "delete":
        if config.IMAP_TRASH_FOLDER:
            return {"type": "delete", "destination": config.IMAP_TRASH_FOLDER}
        logger.error(
            "Cannot move spam email UID %s to trash: trash folder not configured. Set it in the Config page.",
            uid, extra={"email_id": email_id}
        )
    else:
        if config.IMAP_SPAM_FOLDER:
            return {"type": "spam", "destination": config.IMAP_SPAM_FOLDER}
        logger.error(
            "Cannot move spam email UID %s to spam folder: spam folder not configured. Set it in the Config page.",
            uid, extra={"email_id": email_id}
        )
    return None

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

def startup_scan(client):
    logger.info("Scanning %s for untracked emails", config.IMAP_FOLDER)

    current_uids = imap.get_existing_uids(client)
    known_uids = get_known_uids(config.IMAP_FOLDER)
    untracked = current_uids - known_uids

    if not untracked:
        logger.debug("No untracked emails found in %s", config.IMAP_FOLDER)
        return current_uids

    logger.info("Found %s untracked email(s) in %s, processing now", len(untracked), config.IMAP_FOLDER)

    for uid in sorted(untracked, reverse=True):
        logger.debug("Startup scan: processing untracked UID %s", uid)
        try:
            message = imap.fetch_message(client, uid)
            process_email(client, uid, message)
        except Exception as e:
            logger.error("Failed to process email UID %s during startup scan: %s", uid, e)

    logger.debug("Startup scan complete")
    return current_uids

def reprocess_pending_emails(client, current_uids):
    pending = get_unprocessed_emails()
    if not pending:
        logger.debug("No pending emails to reprocess")
        return

    logger.info("Found %s pending email(s) to reprocess", len(pending))

    for row in pending:
        email_id = row["id"]
        uid = int(row["uid"])
        spam_score = row["spam_score"]
        processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        logger.debug(
            "Reprocessing pending email %s (UID %s, spam_score=%s)",
            email_id, uid, spam_score,
            extra={"email_id": email_id}
        )

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
                history=None,
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

        actions = []

        if matched_rule:
            actions = _resolve_rule_actions(matched_rule["actions"], uid, email_id)

        elif spam_score is not None and spam_score >= config.SPAM_THRESHOLD:
            spam_action = _resolve_spam_action(uid, email_id)
            if spam_action:
                actions.append(spam_action)

        logger.debug(
            "Pending email UID %s: %s action(s) to execute: %s",
            uid, len(actions), [a["type"] for a in actions],
            extra={"email_id": email_id}
        )

        all_ok = True
        executed = []
        for action in actions:
            action_type = action["type"]
            logger.info(
                "Reprocessing pending email UID %s: action=%s, destination=%s, rule=%s",
                uid, action_type, action.get("destination") or "none", rule_name,
                extra={"email_id": email_id}
            )
            try:
                imap.execute_action(client, action, uid, email_id=email_id)
                executed.append((action, False))
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

        notes_parts = [build_notes_opener(matched_rule, spam_score, config.DRYRUN)]
        if executed:
            for action, failed in executed:
                if failed:
                    notes_parts.append(failed_action_sentence(action))
                else:
                    notes_parts.append(action_sentence(action, config.DRYRUN))
        else:
            notes_parts.append("No action taken.")
        processed_notes = " ".join(notes_parts)

        current_history = json.loads(row["history"] or "[]")
        new_history_entries = []
        if not config.DRYRUN:
            for action, failed in executed:
                if not failed:
                    entry = {"at": processed_at, "by": "boxwatchr", "action": action["type"]}
                    if "destination" in action:
                        entry["destination"] = action["destination"]
                    new_history_entries.append(entry)

        enqueue_email_update(
            email_id,
            json.dumps(matched_rule) if matched_rule else None,
            actions,
            processed=0 if (not all_ok or config.DRYRUN) else 1,
            processed_at=processed_at,
            processed_notes=processed_notes,
            history=current_history + new_history_entries,
        )
        logger.debug("Enqueued update for pending email %s", email_id, extra={"email_id": email_id})

    logger.debug("Pending email reprocessing complete")

def process_email(client, uid, message):
    email_id = None
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
            mailbox = _decode(addr.mailbox) if addr.mailbox else ""
            host = _decode(addr.host) if addr.host else ""
            sender = "%s@%s" % (mailbox, host) if host else mailbox

        subject = _decode(envelope.subject) if envelope else ""

        date_received = ""
        if envelope and envelope.date:
            date_received = envelope.date.strftime("%Y-%m-%d %H:%M:%S")

        recipients = []
        for addr_list in ([envelope.to, envelope.cc] if envelope else []):
            if addr_list:
                for addr in addr_list:
                    mailbox = _decode(addr.mailbox) if addr.mailbox else ""
                    host = _decode(addr.host) if addr.host else ""
                    if mailbox and host:
                        recipients.append("%s@%s" % (mailbox, host))

        raw_text = _decode(raw_message)
        if "\r\n\r\n" in raw_text:
            raw_headers = raw_text.split("\r\n\r\n", 1)[0]
        elif "\n\n" in raw_text:
            raw_headers = raw_text.split("\n\n", 1)[0]
        else:
            raw_headers = raw_text

        _msg_obj = message_from_string(raw_headers)
        message_id = (_msg_obj.get("Message-ID") or "").strip()

        email_id = uuid.uuid4().hex[:12]

        logger.debug(
            "Email UID %s: sender=%s, subject=%r, recipients=%s",
            uid, sender, subject, recipients,
            extra={"email_id": email_id}
        )
        logger.debug(
            "Email UID %s: size=%s bytes, date=%s, email_id=%s, message_id=%r",
            uid, message_size, date_received, email_id, message_id or "(none)",
            extra={"email_id": email_id}
        )

        existing = get_email_by_message_id(message_id) if message_id else None
        if existing is not None:
            logger.info(
                "Email UID %s already tracked via Message-ID (id=%s, previous uid=%s), updating UID",
                uid, existing["id"], existing["uid"],
                extra={"email_id": existing["id"]}
            )
            email_id = existing["id"]
            update_email_uid(email_id, str(uid))
            email_enqueued = True
            return

        attachments = _parse_attachments(raw_message)
        logger.debug(
            "Email UID %s: %s attachment(s): %s",
            uid, len(attachments), [a["name"] for a in attachments],
            extra={"email_id": email_id}
        )

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

        learn_type = None
        actions = []

        if matched_rule:
            learn_type = matched_rule["learn"]
            actions = _resolve_rule_actions(matched_rule["actions"], uid, email_id)

        elif spam_result["is_spam"]:
            learn_type = "spam"
            spam_action = _resolve_spam_action(uid, email_id)
            if spam_action:
                actions.append(spam_action)

        elif spam_result["score"] <= config.HAM_THRESHOLD:
            learn_type = "ham"

        logger.debug(
            "Email UID %s: spam_score=%.2f, is_spam=%s, rule=%s, learn_type=%s",
            uid, spam_score, spam_result["is_spam"], rule_name, learn_type,
            extra={"email_id": email_id}
        )
        logger.debug(
            "Email UID %s: %s action(s) to execute: %s",
            uid, len(actions), [a["type"] for a in actions],
            extra={"email_id": email_id}
        )

        for action in actions:
            action_type = action["type"]
            logger.debug(
                "Executing action %s (destination=%s) for UID %s",
                action_type, action.get("destination") or "none", uid,
                extra={"email_id": email_id}
            )
            imap.execute_action(client, action, uid, email_id=email_id)
            if action_type in TERMINAL_ACTIONS:
                break

        will_learn = spam.should_learn(learn_type)
        logger.debug(
            "Email UID %s: will_learn=%s, learn_type=%s, DRYRUN=%s, SPAM_LEARNING=%s",
            uid, will_learn, learn_type, config.DRYRUN, config.SPAM_LEARNING,
            extra={"email_id": email_id}
        )

        learned_ok = False
        if not config.DRYRUN and will_learn:
            if learn_type == "spam":
                learned_ok = spam.learn_spam(raw_message, email_id=email_id)
            elif learn_type == "ham":
                learned_ok = spam.learn_ham(raw_message, email_id=email_id)

        notes_parts = [build_notes_opener(matched_rule, spam_score, config.DRYRUN)]
        if actions:
            for action in actions:
                notes_parts.append(action_sentence(action, config.DRYRUN))
        else:
            notes_parts.append("No action taken.")
        if will_learn:
            notes_parts.append(spam.learn_sentence(learn_type, config.DRYRUN))
        processed_notes = " ".join(notes_parts)

        processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        history = []
        if not config.DRYRUN:
            for a in actions:
                entry = {"at": processed_at, "by": "boxwatchr", "action": a["type"]}
                if "destination" in a:
                    entry["destination"] = a["destination"]
                history.append(entry)

        rspamd_learned = None
        if not config.DRYRUN and will_learn and learned_ok:
            rspamd_learned = learn_type
        elif config.DRYRUN and will_learn:
            rspamd_learned = learn_type

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
            history=history,
            message_id=message_id or None,
            rspamd_learned=rspamd_learned,
        )

        email_enqueued = True
        logger.info(
            "Email UID %s processed: actions=[%s], rule=%s, spam_score=%.2f",
            uid, ", ".join(a["type"] for a in actions) if actions else "none", rule_name, spam_score,
            extra={"email_id": email_id}
        )

    except Exception as e:
        logger.error("Failed to process email UID %s: %s", uid, e)
        raise
    finally:
        if email_id is not None and not email_enqueued:
            clear_email_id_from_logs(email_id)
        set_processing(False)

def main():
    _signal.signal(_signal.SIGTERM, _handle_sigterm)

    _print_banner()

    logger.info("boxwatchr starting up")

    health.initialize_database()
    config.load()

    start_dashboard()
    health.start_services_sequentially()

    if not config.SETUP_COMPLETE:
        logger.info("First-run setup required. Open the web dashboard to complete setup, then restart the container.")
        while not _shutdown:
            time.sleep(1)
        logger.info("Shutting down")
        return

    loaded_rules = health.load_rules_startup(config.RULES_PATH)

    health.start_imap(loaded_rules)

    _print_startup_checks(loaded_rules)

    health.start_monitor()

    observer = watch_rules(config.RULES_PATH)

    logger.info("boxwatchr is running")

    try:
        while not _shutdown:
            logger.debug("Starting connection cycle: connecting for startup scan")
            startup_client = imap.connect()
            imap.select_folder(startup_client)
            try:
                current_uids = startup_scan(startup_client)
                reprocess_pending_emails(startup_client, current_uids)
            finally:
                startup_client.logout()
                logger.debug("Startup client logged out")

            if _shutdown:
                break

            try:
                logger.debug("Entering IMAP watch loop")
                imap.watch(process_email)
            except FatalImapError:
                raise
            except Exception as e:
                if _shutdown:
                    break
                logger.warning("IMAP connection dropped, waiting to reconnect: %s", e)
                health.wait_for_services()
                logger.info("Services recovered, reconnecting...")

    except FatalImapError as e:
        _fatal_exit(str(e))
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        observer.stop()
        observer.join()

    logger.info("Shutdown complete")

if __name__ == "__main__":
    main()
