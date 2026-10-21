"""Authenticated encryption for user-supplied provider credentials."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x01"
_ASSOCIATED_DATA = b"xray-spec:uspto-api-key"


class CredentialEncryptionError(ValueError):
    """Encrypted credential material is invalid or cannot be opened."""


def _key(secret: str) -> bytes:
    if len(secret.strip()) < 32:
        raise CredentialEncryptionError("credential encryption key is not configured")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt_credential(value: str, secret: str) -> str:
    plaintext = value.strip().encode("utf-8")
    if not plaintext:
        raise CredentialEncryptionError("credential must not be empty")
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key(secret)).encrypt(nonce, plaintext, _ASSOCIATED_DATA)
    return base64.urlsafe_b64encode(_VERSION + nonce + ciphertext).decode("ascii")


def decrypt_credential(value: str, secret: str) -> str:
    try:
        payload = base64.urlsafe_b64decode(value.encode("ascii"))
        if payload[:1] != _VERSION or len(payload) < 30:
            raise CredentialEncryptionError("unsupported encrypted credential")
        plaintext = AESGCM(_key(secret)).decrypt(
            payload[1:13], payload[13:], _ASSOCIATED_DATA
        )
        return plaintext.decode("utf-8")
    except CredentialEncryptionError:
        raise
    except Exception as exc:
        raise CredentialEncryptionError("credential could not be decrypted") from exc
