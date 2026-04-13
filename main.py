import json
import uuid
import time
import signal as _signal
import threading
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email import message_from_bytes, message_from_string
from boxwatchr import config, imap, spam, rules, health, __version__
from boxwatchr.imap import FatalImapError
from boxwatchr.notes import action_sentence, failed_action_sentence, skipped_learn_sentence, build_notes_opener
from boxwatchr.database import set_processing, clear_email_id_from_logs, enqueue_email, enqueue_email_update, get_known_uids, get_unprocessed_emails, get_email_by_content_hash, update_email_uid, compute_content_hash
from boxwatchr.rules import TERMINAL_ACTIONS
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.main")

_BANNER = r"""
     ____ _________ ____ ____ ____ ____ ____ ____ ____ ____ ____
    ||> |||       |||b |||o |||x |||w |||a |||t |||c |||h |||r ||
    ||__|||_______|||__|||__|||__|||__|||__|||__|||__|||__|||__||
    |/__\|/_______\|/__\|/__\|/__\|/__\|/__\|/__\|/__\|/__\|/__\|
"""

_shutdown = False
_watchers = []
_watchers_lock = threading.Lock()

def _handle_sigterm(signum, frame):
    global _shutdown
    logger.info("Received SIGTERM, shutting down gracefully")
    _shutdown = True
    with _watchers_lock:
        for w in _watchers:
            w.stop()
    imap.request_stop()

def _print_banner():
    lines = _BANNER.split("\n")
    width = max(len(line) for line in lines)
    print(_BANNER, flush=True)
    print(__version__.center(width), flush=True)
    print(flush=True)

def _print_startup_checks(accounts, all_rules):
    divider = "=" * 35
    print(divider, flush=True)
    print("boxwatchr checks", flush=True)
    print(divider, flush=True)
    print("RSPAMD password configured", flush=True)
    print("Accounts configured: %d" % len(accounts), flush=True)
    for acct in accounts:
        print("  [%s] %s:%s -> %s" % (acct["name"], acct["host"], acct["port"], acct["folder"]), flush=True)
    print("Dry run: %s" % ("enabled" if config.DRYRUN else "disabled"), flush=True)
    total_rules = sum(len(r) for r in all_rules.values()) if isinstance(all_rules, dict) else len(all_rules)
    print("Rules loaded: %d" % total_rules, flush=True)
    print(flush=True)

def _fatal_exit(message):
    logger.error("Fatal error: %s", message)
    logger.error("Shutting down.")
    health.fatal_shutdown()


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

def startup_scan(client, account):
    account_id = account["id"]
    folder = account["folder"]
    logger.info("[%s] Scanning %s for untracked emails", account["name"], folder)

    current_uids = imap.get_existing_uids(client)
    known_uids = get_known_uids(folder, account_id=account_id)
    untracked = current_uids - known_uids

    if not untracked:
        logger.debug("[%s] No untracked emails found in %s", account["name"], folder)
        return current_uids

    logger.info("[%s] Found %s untracked email(s) in %s, processing now", account["name"], len(untracked), folder)

    for uid in sorted(untracked, reverse=True):
        logger.debug("[%s] Startup scan: processing untracked UID %s", account["name"], uid)
        try:
            message = imap.fetch_message(client, uid)
            process_email(client, uid, message, account=account, current_uids=current_uids)
        except Exception as e:
            logger.error("[%s] Failed to process email UID %s during startup scan: %s", account["name"], uid, e)

    logger.debug("[%s] Startup scan complete", account["name"])
    return current_uids

def reprocess_pending_emails(client, current_uids, account):
    account_id = account["id"]
    pending = get_unprocessed_emails(account_id=account_id)
    if not pending:
        logger.debug("[%s] No pending emails to reprocess", account["name"])
        return

    logger.info("[%s] Found %s pending email(s) to reprocess", account["name"], len(pending))

    for row in pending:
        email_id = row["id"]
        uid = int(row["uid"])
        spam_score = row["spam_score"]
        processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        logger.debug(
            "[%s] Reprocessing pending email %s (UID %s, spam_score=%s)",
            account["name"], email_id, uid, spam_score,
            extra={"email_id": email_id}
        )

        if uid not in current_uids:
            logger.info(
                "[%s] Pending email UID %s no longer in folder, marking processed",
                account["name"], uid,
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

        matched_rule = rules.evaluate(email_data, spam_score=spam_score, email_id=email_id, account_id=account_id)
        rule_name = matched_rule["name"] if matched_rule else "none"
        logger.info(
            "[%s] Pending email UID %s re-evaluated: rule=%s",
            account["name"], uid, rule_name, extra={"email_id": email_id}
        )

        actions = []
        if matched_rule:
            rule_actions = matched_rule["actions"]
            actions = [a for a in rule_actions if a["type"] not in TERMINAL_ACTIONS] + \
                      [a for a in rule_actions if a["type"] in TERMINAL_ACTIONS]

        logger.debug(
            "[%s] Pending email UID %s: %s action(s) to execute: %s",
            account["name"], uid, len(actions), [a["type"] for a in actions],
            extra={"email_id": email_id}
        )

        all_ok = True
        executed = []
        for action in actions:
            action_type = action["type"]
            if action_type in {"learn_spam", "learn_ham"}:
                if config.DRYRUN:
                    label = "spam" if action_type == "learn_spam" else "ham"
                    logger.info(
                        "DRYRUN: would submit UID %s to rspamd as %s",
                        uid, label, extra={"email_id": email_id}
                    )
                else:
                    logger.info(
                        "Skipping %s action for pending email UID %s: raw message not stored",
                        action_type, uid, extra={"email_id": email_id}
                    )
                executed.append((action, "skipped"))
                continue
            logger.info(
                "[%s] Reprocessing pending email UID %s: action=%s, destination=%s, rule=%s",
                account["name"], uid, action_type, action.get("destination") or "none", rule_name,
                extra={"email_id": email_id}
            )
            try:
                imap.execute_action(client, action, uid, email_id=email_id)
                executed.append((action, False))
            except Exception as e:
                logger.error(
                    "[%s] Failed to execute action %s on pending email UID %s: %s",
                    account["name"], action_type, uid, e,
                    extra={"email_id": email_id}
                )
                executed.append((action, True))
                all_ok = False
            if action_type in TERMINAL_ACTIONS:
                break

        notes_parts = [build_notes_opener(matched_rule, config.DRYRUN)]
        if executed:
            for action, state in executed:
                if state == "skipped":
                    notes_parts.append(skipped_learn_sentence(action))
                elif state:
                    notes_parts.append(failed_action_sentence(action))
                else:
                    notes_parts.append(action_sentence(action, config.DRYRUN))
        else:
            notes_parts.append("No action taken.")
        processed_notes = " ".join(notes_parts)

        current_history = json.loads(row["history"] or "[]")
        new_history_entries = []
        if not config.DRYRUN:
            for action, state in executed:
                if not state:
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
        logger.debug("[%s] Enqueued update for pending email %s", account["name"], email_id, extra={"email_id": email_id})

    logger.debug("[%s] Pending email reprocessing complete", account["name"])

def process_email(client, uid, message, account=None, current_uids=None):
    # Use account dict if provided, fall back to config globals
    account_id = account["id"] if account else config.ACCOUNT_ID
    account_name = account["name"] if account else config.ACCOUNT_NAME
    folder = account["folder"] if account else config.IMAP_FOLDER

    email_id = None
    email_enqueued = False
    set_processing(True)
    try:
        msg_data = message.get(uid, {})
        raw_message = msg_data.get(b"BODY[]", b"")
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

        content_hash = compute_content_hash(sender, subject, date_received, recipients)

        email_id = uuid.uuid4().hex[:12]

        logger.debug(
            "[%s] Email UID %s: sender=%s, subject=%r, recipients=%s",
            account_name, uid, sender, subject, recipients,
            extra={"email_id": email_id}
        )
        logger.debug(
            "[%s] Email UID %s: size=%s bytes, date=%s, email_id=%s, content_hash=%s",
            account_name, uid, message_size, date_received, email_id, content_hash,
            extra={"email_id": email_id}
        )

        existing = get_email_by_content_hash(content_hash)
        if existing is not None:
            clear_email_id_from_logs(email_id)
            existing_uid = int(existing["uid"])
            if current_uids is not None and existing_uid in current_uids:
                logger.info(
                    "[%s] Email UID %s is a duplicate of UID %s (same content hash, both present on server), skipping",
                    account_name, uid, existing["uid"],
                    extra={"email_id": existing["id"]}
                )
            else:
                logger.info(
                    "[%s] Email UID %s already tracked (id=%s, previous uid=%s), updating UID",
                    account_name, uid, existing["id"], existing["uid"],
                    extra={"email_id": existing["id"]}
                )
                email_id = existing["id"]
                update_email_uid(email_id, str(uid))
            email_enqueued = True
            return

        attachments = _parse_attachments(raw_message)
        logger.debug(
            "[%s] Email UID %s: %s attachment(s): %s",
            account_name, uid, len(attachments), [a["name"] for a in attachments],
            extra={"email_id": email_id}
        )

        email_data = {
            "sender": sender,
            "subject": subject,
            "recipients": recipients,
            "raw_headers": raw_headers,
            "attachments": attachments,
        }

        logger.info("[%s] Processing email UID %s from %s", account_name, uid, sender, extra={"email_id": email_id})

        spam_score = spam.get_rspamd_score(raw_message, email_id=email_id)
        if spam_score is None:
            raise RuntimeError("rspamd unreachable")

        matched_rule = rules.evaluate(email_data, spam_score=spam_score, email_id=email_id, account_id=account_id)
        rule_name = matched_rule["name"] if matched_rule else "none"

        actions = []
        if matched_rule:
            rule_actions = matched_rule["actions"]
            actions = [a for a in rule_actions if a["type"] not in TERMINAL_ACTIONS] + \
                      [a for a in rule_actions if a["type"] in TERMINAL_ACTIONS]

        logger.debug(
            "[%s] Email UID %s: spam_score=%.2f, rule=%s, %s action(s): %s",
            account_name, uid, spam_score, rule_name, len(actions), [a["type"] for a in actions],
            extra={"email_id": email_id}
        )

        imap_actions = [a for a in actions if a["type"] not in {"learn_spam", "learn_ham"}]
        learn_actions = [a for a in actions if a["type"] in {"learn_spam", "learn_ham"}]

        executed_imap = []
        for action in imap_actions:
            action_type = action["type"]
            logger.debug(
                "[%s] Executing action %s (destination=%s) for UID %s",
                account_name, action_type, action.get("destination") or "none", uid,
                extra={"email_id": email_id}
            )
            try:
                imap.execute_action(client, action, uid, email_id=email_id)
                executed_imap.append((action, False))
            except Exception as e:
                logger.error(
                    "Failed to execute action %s on UID %s: %s",
                    action_type, uid, e,
                    extra={"email_id": email_id}
                )
                executed_imap.append((action, True))
            if action_type in TERMINAL_ACTIONS:
                break

        imap_all_ok = not any(had_error for _, had_error in executed_imap)

        rspamd_learned = None
        for action in learn_actions:
            action_type = action["type"]
            if config.DRYRUN:
                logger.debug(
                    "DRYRUN: would execute action %s for UID %s",
                    action_type, uid,
                    extra={"email_id": email_id}
                )
            else:
                logger.debug(
                    "Executing action %s for UID %s",
                    action_type, uid,
                    extra={"email_id": email_id}
                )
            if action_type == "learn_spam":
                if not config.DRYRUN:
                    ok = spam.learn_spam(raw_message, email_id=email_id)
                    if ok:
                        rspamd_learned = "spam"
            elif action_type == "learn_ham":
                if not config.DRYRUN:
                    ok = spam.learn_ham(raw_message, email_id=email_id)
                    if ok:
                        rspamd_learned = "ham"

        notes_parts = [build_notes_opener(matched_rule, config.DRYRUN)]
        if actions:
            for action, had_error in executed_imap:
                if had_error:
                    notes_parts.append(failed_action_sentence(action))
                else:
                    notes_parts.append(action_sentence(action, config.DRYRUN))
            for action in learn_actions:
                if config.DRYRUN or rspamd_learned is not None:
                    notes_parts.append(action_sentence(action, config.DRYRUN))
                else:
                    notes_parts.append(failed_action_sentence(action))
        else:
            notes_parts.append("No action taken.")
        processed_notes = " ".join(notes_parts)

        processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        history = []
        if not config.DRYRUN:
            for a, had_error in executed_imap:
                if not had_error:
                    entry = {"at": processed_at, "by": "boxwatchr", "action": a["type"]}
                    if "destination" in a:
                        entry["destination"] = a["destination"]
                    history.append(entry)

        enqueue_email(
            uid=str(uid),
            folder=folder,
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
            processed=0 if (not imap_all_ok or config.DRYRUN) else 1,
            processed_at=processed_at,
            processed_notes=processed_notes,
            email_id=email_id,
            history=history,
            message_id=message_id or None,
            rspamd_learned=rspamd_learned,
            account_id=account_id,
            content_hash=content_hash,
        )

        email_enqueued = True
        logger.info(
            "[%s] Email UID %s processed: actions=[%s], rule=%s, spam_score=%.2f",
            account_name, uid, ", ".join(a["type"] for a in actions) if actions else "none", rule_name, spam_score,
            extra={"email_id": email_id}
        )

    except Exception as e:
        logger.error("[%s] Failed to process email UID %s: %s", account_name if account else "default", uid, e)
        raise
    finally:
        if email_id is not None and not email_enqueued:
            clear_email_id_from_logs(email_id)
        set_processing(False)


class AccountWatcher:
    """Runs the IMAP watch loop for a single account in its own thread."""

    BACKOFF_MIN = 5
    BACKOFF_MAX = 120

    def __init__(self, account):
        self.account = account
        self.account_id = account["id"]
        self.account_name = account["name"]
        self._stop_event = threading.Event()
        self._reconnect_event = threading.Event()
        self._thread = None
        self._backoff = self.BACKOFF_MIN

    def start(self):
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="watcher-%s" % self.account_name
        )
        self._thread.start()
        logger.info("[%s] Account watcher started", self.account_name)

    def stop(self):
        self._stop_event.set()
        self._reconnect_event.set()

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        while not self._stop_event.is_set():
            try:
                logger.debug("[%s] Starting connection cycle", self.account_name)
                startup_client = imap.connect(account=self.account)
                imap.select_folder(startup_client, self.account["folder"])
                try:
                    current_uids = startup_scan(startup_client, self.account)
                    reprocess_pending_emails(startup_client, current_uids, self.account)
                finally:
                    startup_client.logout()
                    logger.debug("[%s] Startup client logged out", self.account_name)

                if self._stop_event.is_set():
                    break

                self._backoff = self.BACKOFF_MIN

                logger.debug("[%s] Entering IMAP watch loop", self.account_name)
                imap.watch(
                    callback=lambda c, uid, msg: process_email(c, uid, msg, account=self.account),
                    account=self.account,
                    stop_event=self._stop_event,
                    reconnect_event=self._reconnect_event,
                    rescan_callback=lambda c: startup_scan(c, self.account),
                )

            except FatalImapError as e:
                logger.error("[%s] Fatal IMAP error: %s — watcher stopping", self.account_name, e)
                break
            except Exception as e:
                if self._stop_event.is_set():
                    break
                logger.warning("[%s] IMAP connection dropped: %s — reconnecting in %ss", self.account_name, e, self._backoff)
                self._stop_event.wait(self._backoff)
                self._backoff = min(self._backoff * 2, self.BACKOFF_MAX)

        logger.info("[%s] Account watcher stopped", self.account_name)


def main():
    global _shutdown

    _signal.signal(_signal.SIGTERM, _handle_sigterm)

    _print_banner()

    logger.info("boxwatchr starting up")

    health.initialize_database()
    config.load()

    health.start_web()
    health.start_services_sequentially()

    if not config.SETUP_COMPLETE:
        logger.info("First-run setup required. Open the web dashboard to complete setup, then restart the container.")
        while not _shutdown:
            time.sleep(1)
        logger.info("Shutting down")
        return

    accounts = config.get_all_account_dicts()
    if not accounts:
        logger.error("No accounts configured. Open the web dashboard to add an account.")
        while not _shutdown:
            time.sleep(1)
        logger.info("Shutting down")
        return

    # Load rules for all accounts
    all_rules = {}
    for acct in accounts:
        loaded = rules.load_rules(acct["id"])
        all_rules[acct["id"]] = loaded
        logger.info("[%s] Loaded %s rule(s)", acct["name"], len(loaded))

    # Verify IMAP connectivity for each account
    for acct in accounts:
        health.start_imap_for_account(acct, all_rules.get(acct["id"], []))

    _print_startup_checks(accounts, all_rules)

    health.start_monitor()

    logger.info("boxwatchr is running (%s account(s))", len(accounts))

    # Start a watcher thread for each account
    with _watchers_lock:
        for acct in accounts:
            watcher = AccountWatcher(acct)
            _watchers.append(watcher)
            watcher.start()

    try:
        while not _shutdown:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down")

    # Stop all watchers
    with _watchers_lock:
        for w in _watchers:
            w.stop()

    logger.info("Shutdown complete")

if __name__ == "__main__":
    main()
