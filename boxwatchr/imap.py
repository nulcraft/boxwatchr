import time
import socket
from imapclient import IMAPClient
from boxwatchr import config
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.imap")

IDLE_TIMEOUT = 540

def connect():
    logger.info("Connecting to IMAP server %s:%s", config.IMAP_HOST, config.IMAP_PORT)
    try:
        client = IMAPClient(config.IMAP_HOST, port=config.IMAP_PORT, ssl=True)
        client.login(config.IMAP_USERNAME, config.IMAP_PASSWORD)
        logger.info("Logged in as %s", config.IMAP_USERNAME)
        return client
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
    while True:
        try:
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

        except Exception as e:
            logger.error("IMAP connection lost: %s", e)
            logger.info("Reconnecting in 30 seconds")
            time.sleep(30)

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

                if new_uids:
                    logger.info("Detected %s new message(s)", len(new_uids))
                    for uid in new_uids:
                        logger.debug("Processing new message UID %s", uid)
                        message = fetch_message(client, uid)
                        callback(uid, message)

                    known_uids = current_uids

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

            if new_uids:
                logger.info("Detected %s new message(s)", len(new_uids))
                for uid in new_uids:
                    logger.debug("Processing new message UID %s", uid)
                    message = fetch_message(client, uid)
                    callback(uid, message)

                known_uids = current_uids
            else:
                logger.debug("No new messages found")

        except Exception as e:
            logger.error("Error during poll watch: %s", e)
            raise