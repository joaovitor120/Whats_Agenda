import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet() -> Fernet:
    key = settings.token_encryption_key
    if key:
        try:
            return Fernet(key.encode())
        except Exception as exc:
            raise ValueError("TOKEN_ENCRYPTION_KEY must be a Fernet key") from exc
    # Development convenience only; production must configure a persistent secret.
    if settings.app_env == "production":
        raise ValueError("TOKEN_ENCRYPTION_KEY is required in production")
    derived = base64.urlsafe_b64encode(hashlib.sha256(settings.app_secret_key.encode()).digest())
    return Fernet(derived)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def new_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def verify_secret(provided: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())
