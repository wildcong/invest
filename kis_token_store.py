"""Authenticated encryption for the short-lived KIS access token.

Only ciphertext is committed to the repository.  The key is derived from the
existing KIS app secret, which remains in GitHub Actions secrets.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TOKEN_AAD = b"wildcong/invest:kis-access-token:v1"
TOKEN_KEY_CONTEXT = b"wildcong/invest:kis-token-key:v1\0"
TOKEN_NONCE_BYTES = 12


def _derive_key(app_secret: str) -> bytes:
    if not app_secret:
        raise ValueError("KIS app secret is required for token encryption")
    return hashlib.sha256(TOKEN_KEY_CONTEXT + app_secret.encode("utf-8")).digest()


def encrypt_access_token(token: str, app_secret: str) -> str:
    if not token:
        raise ValueError("access token is required")
    nonce = os.urandom(TOKEN_NONCE_BYTES)
    ciphertext = AESGCM(_derive_key(app_secret)).encrypt(
        nonce,
        token.encode("utf-8"),
        TOKEN_AAD,
    )
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_access_token(ciphertext: str, app_secret: str) -> str | None:
    try:
        payload = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
        nonce = payload[:TOKEN_NONCE_BYTES]
        encrypted = payload[TOKEN_NONCE_BYTES:]
        if len(nonce) != TOKEN_NONCE_BYTES or not encrypted:
            return None
        plaintext = AESGCM(_derive_key(app_secret)).decrypt(
            nonce,
            encrypted,
            TOKEN_AAD,
        )
        return plaintext.decode("utf-8")
    except (InvalidTag, ValueError, TypeError, UnicodeError):
        return None
