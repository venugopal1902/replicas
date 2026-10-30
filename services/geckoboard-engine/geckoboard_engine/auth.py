from __future__ import annotations

import hashlib
import hmac
import secrets

PASSWORD_ROUNDS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ROUNDS)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, expected = stored.split("$", 2)
        if scheme != "pbkdf2":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ROUNDS).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def hash_session(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_api_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_api_key(token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(token), stored_hash)
