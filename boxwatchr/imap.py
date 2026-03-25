import time
import threading
from imapclient import IMAPClient
from imapclient.exceptions import LoginError
from boxwatchr import config
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.imap")

IDLE_TIMEOUT = 1740  # 29 minutes, RFC 2177 recommended maximum before server-side timeout (typically 30m)
RESCAN_INTERVAL = 300  # 5 minutes

_stop_event = threading.Event()
_reconnect_event = threading.Event()

_folder_list_cache = {"folders": [], "expires": 0.0, "fetching": False}
_folder_list_lock = threading.Lock()

def get_folder_list():
    with _folder_list_lock:
        now = time.monotonic()
        if _folder_list_cache["expires"] > now:
            return _folder_list_cache["folders"]
        if _folder_list_cache["fetching"]:
            return _folder_list_cache["folders"]
        _folder_list_cache["fetching"] = True

    try:
        client = connect()
        try:
            folders = sorted(name for flags, delimiter, name in client.list_folders())
        finally:
            client.logout()
    except Exception as e:
        logger.warning("Could not fetch IMAP folder list: %s", e)
        folders = []

    with _folder_list_lock:
        _folder_list_cache["folders"] = folders
        _folder_list_cache["expires"] = time.monotonic() + 10.0
        _folder_list_cache["fetching"] = False

    return folders

class FatalImapError(Exception):
    pass

def request_stop():
    _stop_event.set()
    _reconnect_event.set()

def request_reconnect():
    _reconnect_event.set()

def connect(tls_mode=None):
    mode = tls_mode if tls_mode is not None else config.IMAP_TLS_MODE
    logger.debug("Connecting to IMAP server %s:%s (tls_mode=%s)", config.IMAP_HOST, config.IMAP_PORT, mode)
    try:
        use_ssl = mode == "ssl"
        client = IMAPClient(config.IMAP_HOST, port=config.IMAP_PORT, ssl=use_ssl, timeout=60)
        if mode == "starttls":
            client.starttls()
        logger.debug("TCP connection established to %s:%s", config.IMAP_HOST, config.IMAP_PORT)
        client.login(config.IMAP_USERNAME, config.IMAP_PASSWORD)
        logger.debug("Logged in as %s", config.IMAP_USERNAME)
        capabilities = client.capabilities()
        logger.debug(
            "Server capabilities: %s",
            ", ".join(c.decode() if isinstance(c, bytes) else c for c in capabilities)
        )
        return client
    except LoginError as e:
        logger.error("Authentication failed for %s: %s", config.IMAP_USERNAME, e)
        raise FatalImapError("Authentication failed") from e
    except Exception as e:
        logger.error("Failed to connect to IMAP server: %s", e)
        raise

def select_folder(client):
    logger.debug("Selecting folder: %s", config.IMAP_FOLDER)
    try:
        info = client.select_folder(config.IMAP_FOLDER)
        logger.debug("Folder %s selected: %s message(s)", config.IMAP_FOLDER, info.get(b"EXISTS", "?"))
    except Exception as e:
        logger.error("Failed to select folder %s: %s", config.IMAP_FOLDER, e)
        raise

def fetch_message(client, uid):
    logger.debug("Fetching message UID %s (BODY.PEEK[] + SIZE + ENVELOPE)", uid)
    try:
        response = client.fetch([uid], ["BODY.PEEK[]", "RFC822.SIZE", "ENVELOPE"])
        msg_data = response.get(uid, {})
        size = msg_data.get(b"RFC822.SIZE", 0)
        logger.debug("Fetched message UID %s: %s bytes", uid, size)
        return response
    except Exception as e:
        logger.error("Failed to fetch message UID %s: %s", uid, e)
        raise

def list_folder_names(client):
    logger.debug("Listing IMAP folders")
    try:
        folders = client.list_folders()
        names = [name for _flags, _delim, name in folders]
        logger.debug("Found %s folder(s)", len(names))
        return names
    except Exception as e:
        logger.error("Failed to list IMAP folders: %s", e)
        raise


def get_existing_uids(client):
    logger.debug("Fetching existing UIDs in %s", config.IMAP_FOLDER)
    try:
        uids = client.search(["ALL"])
        logger.debug("Found %s existing messages in %s", len(uids), config.IMAP_FOLDER)
        return set(uids)
    except Exception as e:
        logger.error("Failed to fetch existing UIDs: %s", e)
        raise

def watch(callback, rescan_callback=None):
    _reconnect_event.clear()
    client = connect()
    select_folder(client)
    known_uids = get_existing_uids(client)
    logger.info("Watching %s for new mail (%s existing messages)", config.IMAP_FOLDER, len(known_uids))

    try:
        if client.has_capability("IDLE"):
            logger.info("IMAP IDLE is supported, using push notifications")
            _watch_idle(client, known_uids, callback, rescan_callback=rescan_callback)
        else:
            logger.warning("IMAP IDLE is not supported, falling back to polling every %s seconds", config.IMAP_POLL_INTERVAL)
            _watch_poll(client, known_uids, callback)
    finally:
        try:
            client.logout()
        except Exception:
            pass

def _watch_idle(client, known_uids, callback, rescan_callback=None):
    last_rescan = time.monotonic()
    while not _stop_event.is_set() and not _reconnect_event.is_set():
        idle_started = False
        try:
            logger.debug("Starting IDLE session (timeout=%ss)", IDLE_TIMEOUT)
            client.idle()
            idle_started = True

            responses = []
            rescan_due = False
            deadline = time.monotonic() + IDLE_TIMEOUT
            while time.monotonic() < deadline:
                if _stop_event.is_set() or _reconnect_event.is_set():
                    break
                if rescan_callback and time.monotonic() - last_rescan >= RESCAN_INTERVAL:
                    rescan_due = True
                    break
                chunk = client.idle_check(timeout=1)
                if chunk:
                    responses = chunk
                    break

            client.idle_done()
            idle_started = False
            logger.debug("IDLE session ended: received %s server response(s)", len(responses))
            if responses:
                logger.debug("IDLE responses: %s", responses)

            if _stop_event.is_set() or _reconnect_event.is_set():
                break

            if rescan_due:
                logger.info("Running periodic rescan of %s", config.IMAP_FOLDER)
                rescan_callback(client)
                last_rescan = time.monotonic()
                continue

            if responses:
                current_uids = get_existing_uids(client)
                new_uids = current_uids - known_uids
                removed_uids = known_uids - current_uids
                known_uids = current_uids

                if removed_uids:
                    logger.debug("UIDs removed from folder since last check: %s", sorted(removed_uids))
                if new_uids:
                    logger.info("Detected %s new message(s): UIDs %s", len(new_uids), sorted(new_uids))
                    for uid in new_uids:
                        logger.debug("Dispatching callback for new message UID %s", uid)
                        message = fetch_message(client, uid)
                        callback(client, uid, message)
                else:
                    logger.debug("IDLE response received but no new messages (flags changed or expunge)")

        except Exception as e:
            if idle_started:
                try:
                    client.idle_done()
                except Exception:
                    pass
            logger.warning("IDLE connection interrupted: %s", e)
            raise

def _watch_poll(client, known_uids, callback):
    while not _stop_event.is_set() and not _reconnect_event.is_set():
        try:
            logger.debug("Polling: sleeping %s seconds", config.IMAP_POLL_INTERVAL)
            time.sleep(config.IMAP_POLL_INTERVAL)

            if _stop_event.is_set() or _reconnect_event.is_set():
                break

            logger.debug("Polling for new messages in %s", config.IMAP_FOLDER)

            current_uids = get_existing_uids(client)
            new_uids = current_uids - known_uids
            removed_uids = known_uids - current_uids
            known_uids = current_uids

            if removed_uids:
                logger.debug("UIDs removed from folder since last poll: %s", sorted(removed_uids))
            if new_uids:
                logger.info("Detected %s new message(s): UIDs %s", len(new_uids), sorted(new_uids))
                for uid in new_uids:
                    logger.debug("Dispatching callback for new message UID %s", uid)
                    message = fetch_message(client, uid)
                    callback(client, uid, message)
            else:
                logger.debug("Poll complete: no new messages")

        except Exception as e:
            logger.warning("Poll connection interrupted: %s", e)
            raise

def flag_message(client, uid, email_id=None):
    if config.DRYRUN:
        logger.info("DRYRUN: would flag UID %s", uid, extra={"email_id": email_id})
        return
    logger.debug("Flagging UID %s", uid, extra={"email_id": email_id})
    try:
        client.add_flags([uid], [b"\\Flagged"])
        logger.debug("Flagged UID %s", uid, extra={"email_id": email_id})
    except Exception as e:
        logger.error("Failed to flag UID %s: %s", uid, e, extra={"email_id": email_id})
        raise

def unflag_message(client, uid, email_id=None):
    if config.DRYRUN:
        logger.info("DRYRUN: would unflag UID %s", uid, extra={"email_id": email_id})
        return
    logger.debug("Unflagging UID %s", uid, extra={"email_id": email_id})
    try:
        client.remove_flags([uid], [b"\\Flagged"])
        logger.debug("Unflagged UID %s", uid, extra={"email_id": email_id})
    except Exception as e:
        logger.error("Failed to unflag UID %s: %s", uid, e, extra={"email_id": email_id})
        raise

def mark_read(client, uid, email_id=None):
    if config.DRYRUN:
        logger.info("DRYRUN: would mark UID %s as read", uid, extra={"email_id": email_id})
        return
    logger.debug("Marking UID %s as read", uid, extra={"email_id": email_id})
    try:
        client.add_flags([uid], [b"\\Seen"])
        logger.debug("Marked UID %s as read", uid, extra={"email_id": email_id})
    except Exception as e:
        logger.error("Failed to mark UID %s as read: %s", uid, e, extra={"email_id": email_id})
        raise

def mark_unread(client, uid, email_id=None):
    if config.DRYRUN:
        logger.info("DRYRUN: would mark UID %s as unread", uid, extra={"email_id": email_id})
        return
    logger.debug("Marking UID %s as unread", uid, extra={"email_id": email_id})
    try:
        client.remove_flags([uid], [b"\\Seen"])
        logger.debug("Marked UID %s as unread", uid, extra={"email_id": email_id})
    except Exception as e:
        logger.error("Failed to mark UID %s as unread: %s", uid, e, extra={"email_id": email_id})
        raise

def move_message(client, uid, destination, email_id=None):
    if config.DRYRUN:
        logger.info("DRYRUN: would move UID %s to %s", uid, destination, extra={"email_id": email_id})
        return
    logger.debug("Moving UID %s to %s", uid, destination, extra={"email_id": email_id})
    try:
        if client.has_capability("MOVE"):
            logger.debug("Using IMAP MOVE extension for UID %s", uid, extra={"email_id": email_id})
            client.move([uid], destination)
        else:
            logger.debug("IMAP MOVE not available, using COPY+DELETE+EXPUNGE for UID %s", uid, extra={"email_id": email_id})
            client.copy([uid], destination)
            client.delete_messages([uid])
            if client.has_capability("UIDPLUS"):
                logger.debug("Using UIDPLUS expunge for UID %s", uid, extra={"email_id": email_id})
                client.expunge([uid])
            else:
                logger.warning(
                    "UIDPLUS not available — using bare EXPUNGE for UID %s. "
                    "This will expunge ALL messages flagged \\Deleted in the folder, not just this one.",
                    uid, extra={"email_id": email_id}
                )
                client.expunge()
        logger.debug("Moved UID %s to %s successfully", uid, destination, extra={"email_id": email_id})
    except Exception as e:
        logger.error("Failed to move UID %s to %s: %s", uid, destination, e, extra={"email_id": email_id})
        raise

def execute_action(client, action, uid, email_id=None):
    action_type = action["type"]
    dest = action.get("destination")
    if action_type == "mark_read":
        mark_read(client, uid, email_id=email_id)
    elif action_type == "mark_unread":
        mark_unread(client, uid, email_id=email_id)
    elif action_type == "flag":
        flag_message(client, uid, email_id=email_id)
    elif action_type == "unflag":
        unflag_message(client, uid, email_id=email_id)
    elif action_type in {"move", "delete", "spam"}:
        move_message(client, uid, dest, email_id=email_id)
    else:
        logger.warning("Unknown action type %r for UID %s", action_type, uid, extra={"email_id": email_id})
