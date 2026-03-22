import os
import threading
from boxwatchr.logger import get_logger

logger = get_logger("boxwatchr.crypto")

_KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "secret.key")

_fernet = None
_fernet_lock = threading.Lock()

def _get_fernet():
    global _fernet
    with _fernet_lock:
        if _fernet is not None:
            return _fernet
        from cryptography.fernet import Fernet
        if not os.path.exists(_KEY_PATH):
            os.makedirs(os.path.dirname(_KEY_PATH), exist_ok=True)
            key = Fernet.generate_key()
            with open(_KEY_PATH, "wb") as f:
                f.write(key)
            logger.info("Generated new encryption key at %s", _KEY_PATH)
        with open(_KEY_PATH, "rb") as f:
            key = f.read().strip()
        _fernet = Fernet(key)
        return _fernet

def encrypt_password(plaintext):
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")

def decrypt_password(ciphertext):
    if not ciphertext:
        return ""
    from cryptography.fernet import InvalidToken
    try:
        f = _get_fernet()
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("IMAP password is not encrypted; treating as plaintext until next config save")
        return ciphertext
