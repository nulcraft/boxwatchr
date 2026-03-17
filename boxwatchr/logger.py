import logging
import os

def get_logger(name):
    level = os.environ.get("LOG_LEVEL", "INFO").upper()

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)

        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger