from __future__ import annotations

import base64
import json
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:  # pragma: no cover - cryptography ships with the project requirements
    Fernet = None


@lru_cache(maxsize=1)
def _fernet() -> "Fernet | None":
    if Fernet is None:
        return None
    secret = settings.NEXAFLOW["ENCRYPTION_KEY"]
    if not secret:
        if not settings.DEBUG:
            raise ImproperlyConfigured("NEXAFLOW_ENCRYPTION_KEY must be set when DEBUG is false.")
        secret = settings.SECRET_KEY
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"nexaflow-credential-vault-v1",
        iterations=180_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    return Fernet(key)


def encrypt_credentials(data: dict) -> str:
    payload = json.dumps(data or {}, separators=(",", ":"))
    cipher = _fernet()
    if cipher is None:
        return "__plain__" + payload
    return cipher.encrypt(payload.encode()).decode()


def decrypt_credentials(token: str) -> dict:
    if not token:
        return {}
    if token.startswith("__plain__"):
        return json.loads(token[len("__plain__") :])
    cipher = _fernet()
    if cipher is None:
        return {}
    try:
        return json.loads(cipher.decrypt(token.encode()).decode())
    except Exception:
        return {}


def mask_credentials(data: dict) -> dict:
    masked = {}
    for key, value in (data or {}).items():
        text = str(value)
        masked[key] = "••••••••" + text[-4:] if len(text) > 4 else "••••••••"
    return masked
