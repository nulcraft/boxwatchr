import time
from imapclient import IMAPClient
from imapclient.exceptions import LoginError
from boxwatchr import config
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.imap")

IDLE_TIMEOUT = 540

class FatalImapError(Exception):
    pass

def connect():
    logger.info("Connecting to IMAP server %s:%s", config.IMAP_HOST, config.IMAP_PORT)
    try:
        client = IMAPClient(config.IMAP_HOST, port=config.IMAP_PORT, ssl=True)
        client.login(config.IMAP_USERNAME, config.IMAP_PASSWORD)
        logger.info("Logged in as %s", config.IMAP_USERNAME)
        return client
    except LoginError as e:
        logger.error("Authentication failed for %s: %s", config.IMAP_USERNAME, e)
        raise FatalImapError("Authentication failed") from e
    except Exception as e:
        logger.error("Failed to connect to IMAP server: %s", e)
        raise

def select_folder(client):
    logger.info("Selecting folder: %s", config.IMAP_FOLDER)
    try:
        client.select_folder(config.IMAP_FOLDER)
        logger.debug("Folder selected successfully")
    except Exception as e:
        logger.error("Failed to select folder %s: %s", config.IMAP_FOLDER, e)
        raise

def fetch_message(client, uid):
    logger.debug("Fetching message UID %s", uid)
    try:
        response = client.fetch([uid], ["RFC822", "RFC822.SIZE", "ENVELOPE"])
        logger.debug("Fetched message UID %s successfully", uid)
        return response
    except Exception as e:
        logger.error("Failed to fetch message UID %s: %s", uid, e)
        raise

def list_folder_names(client):
    try:
        folders = client.list_folders()
        return {name for _flags, _delim, name in folders}
    except Exception as e:
        logger.error("Failed to list IMAP folders: %s", e)
        raise

def detect_special_folders(client):
    logger.debug("Detecting special-use folders from server")
    try:
        folders = client.list_folders()
    except Exception as e:
        logger.warning("Could not list folders for special-use detection: %s", e)
        return None, None

    trash = None
    junk = None
    for flags, _delimiter, name in folders:
        flag_set = {f.decode() if isinstance(f, bytes) else f for f in flags}
        if "\\Trash" in flag_set and trash is None:
            trash = name
        if ("\\Junk" in flag_set or "\\Spam" in flag_set) and junk is None:
            junk = name

    logger.debug("Special-use detection: trash=%s, junk=%s", trash, junk)
    return trash, junk

def get_existing_uids(client):
    logger.debug("Fetching existing UIDs in %s", config.IMAP_FOLDER)
    try:
        uids = client.search(["ALL"])
        logger.debug("Found %s existing messages", len(uids))
        return set(uids)
    except Exception as e:
        logger.error("Failed to fetch existing UIDs: %s", e)
        raise

def watch(callback):
    client = connect()
    select_folder(client)
    known_uids = get_existing_uids(client)
    logger.info("Watching %s for new mail (%s existing messages)", config.IMAP_FOLDER, len(known_uids))

    if client.has_capability("IDLE"):
        logger.info("IMAP IDLE is supported, using push notifications")
        _watch_idle(client, known_uids, callback)
    else:
        logger.warning("IMAP IDLE is not supported, falling back to polling every %s seconds", config.IMAP_POLL_INTERVAL)
        _watch_poll(client, known_uids, callback)

def _watch_idle(client, known_uids, callback):
    while True:
        try:
            logger.debug("Starting IDLE session")
            client.idle()

            responses = client.idle_check(timeout=IDLE_TIMEOUT)

            client.idle_done()
            logger.debug("IDLE session ended, received %s responses", len(responses))

            if responses:
                current_uids = get_existing_uids(client)
                new_uids = current_uids - known_uids
                known_uids = current_uids

                if new_uids:
                    logger.info("Detected %s new message(s)", len(new_uids))
                    for uid in new_uids:
                        logger.debug("Processing new message UID %s", uid)
                        message = fetch_message(client, uid)
                        callback(client, uid, message)
                else:
                    logger.debug("IDLE response received, no new messages")

        except Exception as e:
            logger.error("Error during IDLE watch: %s", e)
            raise

def _watch_poll(client, known_uids, callback):
    while True:
        try:
            time.sleep(config.IMAP_POLL_INTERVAL)
            logger.debug("Polling for new messages")

            current_uids = get_existing_uids(client)
            new_uids = current_uids - known_uids
            known_uids = current_uids

            if new_uids:
                logger.info("Detected %s new message(s)", len(new_uids))
                for uid in new_uids:
                    logger.debug("Processing new message UID %s", uid)
                    message = fetch_message(client, uid)
                    callback(client, uid, message)
            else:
                logger.debug("No new messages found")

        except Exception as e:
            logger.error("Error during poll watch: %s", e)
            raise

def mark_read(client, uid, email_id=None):
    if config.DRYRUN:
        logger.info("DRYRUN: would mark UID %s as read", uid, extra={"email_id": email_id})
        return
    logger.debug("Marking UID %s as read", uid, extra={"email_id": email_id})
    try:
        client.add_flags([uid], [b"\\Seen"])
        logger.info("Marked UID %s as read", uid, extra={"email_id": email_id})
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
        logger.info("Marked UID %s as unread", uid, extra={"email_id": email_id})
    except Exception as e:
        logger.error("Failed to mark UID %s as unread: %s", uid, e, extra={"email_id": email_id})
        raise

def move_message(client, uid, destination, email_id=None):
    if config.DRYRUN:
        logger.info("DRYRUN: would move UID %s to %s", uid, destination, extra={"email_id": email_id})
        return
    logger.debug("Moving UID %s to %s", uid, destination, extra={"email_id": email_id})
    try:
        client.copy([uid], destination)
        client.delete_messages([uid])
        client.expunge()
        logger.info("Moved UID %s to %s", uid, destination, extra={"email_id": email_id})
    except Exception as e:
        logger.error("Failed to move UID %s to %s: %s", uid, destination, e, extra={"email_id": email_id})
        raise

