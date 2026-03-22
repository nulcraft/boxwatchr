import requests
from boxwatchr import config
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.spam")

def get_rspamd_score(raw_message, email_id=None):
    url = "http://%s:%s/checkv2" % (config.RSPAMD_HOST, config.RSPAMD_PORT)
    logger.debug("Submitting message to rspamd at %s", url, extra={"email_id": email_id})

    try:
        response = requests.post(
            url,
            data=raw_message,
            headers={"Content-Type": "text/plain"},
            timeout=10
        )

        if response.status_code != 200:
            logger.error(
                "rspamd returned status %s: %s",
                response.status_code,
                response.text,
                extra={"email_id": email_id}
            )
            return None

        result = response.json()
        score = result.get("score", 0.0)
        symbols = result.get("symbols", {})

        logger.debug(
            "rspamd score: %.2f, symbols fired: %s",
            score,
            len(symbols),
            extra={"email_id": email_id}
        )

        if symbols:
            for symbol, details in symbols.items():
                logger.debug(
                    "Symbol: %s (score: %.2f)",
                    symbol,
                    details.get("score", 0.0),
                    extra={"email_id": email_id}
                )

        return score

    except requests.exceptions.Timeout:
        logger.error("rspamd request timed out", extra={"email_id": email_id})
        return None

    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to rspamd at %s", url, extra={"email_id": email_id})
        return None

    except Exception as e:
        logger.error("Unexpected error during rspamd score check: %s", e, extra={"email_id": email_id})
        return None

def learn_spam(raw_message, email_id=None):
    return _learn(raw_message, "spam", email_id)

def learn_ham(raw_message, email_id=None):
    return _learn(raw_message, "ham", email_id)

def _learn(raw_message, learn_type, email_id=None):
    url = "http://%s:%s/learn%s" % (config.RSPAMD_HOST, config.RSPAMD_CONTROLLER_PORT, learn_type)
    logger.debug("Submitting message to rspamd for %s learning at %s", learn_type, url, extra={"email_id": email_id})

    try:
        response = requests.post(
            url,
            data=raw_message,
            headers={
                "Content-Type": "text/plain",
                "Password": config.RSPAMD_PASSWORD
            },
            timeout=10
        )

        if response.status_code != 200:
            logger.warning(
                "rspamd learning returned status %s: %s",
                response.status_code,
                response.text,
                extra={"email_id": email_id}
            )
            return False

        logger.info(
            "rspamd %s learning response: %s",
            learn_type,
            response.text.strip(),
            extra={"email_id": email_id}
        )
        return True

    except requests.exceptions.Timeout:
        logger.error("rspamd learning request timed out", extra={"email_id": email_id})
        return False

    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to rspamd at %s", url, extra={"email_id": email_id})
        return False

    except Exception as e:
        logger.error("Unexpected error during rspamd learning: %s", e, extra={"email_id": email_id})
        return False
