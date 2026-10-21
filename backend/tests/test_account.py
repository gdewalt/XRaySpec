"""Account settings keep provider credentials encrypted and write-only."""

from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings
from app.db.models import User
from app.services.credentials import CredentialEncryptionError, decrypt_credential


async def test_uspto_api_key_is_encrypted_and_never_returned(client, make_token, auth):
    token = make_token("account-owner", "owner@example.com")
    headers = auth(token)

    account = await client.get("/api/v1/account", headers=headers)
    assert account.status_code == 200
    assert account.json() == {
        "email": "owner@example.com",
        "uspto_api_key_configured": False,
    }

    secret_value = "uspto-personal-api-key-12345"
    saved = await client.put(
        "/api/v1/account/uspto-api-key",
        headers=headers,
        json={"api_key": secret_value},
    )
    assert saved.status_code == 200
    assert saved.json()["uspto_api_key_configured"] is True
    assert secret_value not in saved.text

    async with client.sessionmaker() as session:
        user = (await session.execute(select(User))).scalar_one()
        assert user.uspto_api_key_encrypted
        assert secret_value not in user.uspto_api_key_encrypted
        assert (
            decrypt_credential(
                user.uspto_api_key_encrypted,
                get_settings().credential_encryption_key,
            )
            == secret_value
        )

    removed = await client.delete("/api/v1/account/uspto-api-key", headers=headers)
    assert removed.status_code == 204
    account = await client.get("/api/v1/account", headers=headers)
    assert account.json()["uspto_api_key_configured"] is False


def test_credential_cipher_rejects_wrong_key():
    from app.services.credentials import encrypt_credential

    encrypted = encrypt_credential(
        "provider-secret",
        "first-encryption-key-0123456789-abcdef",
    )
    try:
        decrypt_credential(
            encrypted,
            "second-encryption-key-0123456789-abcde",
        )
    except CredentialEncryptionError:
        pass
    else:
        raise AssertionError("credential decrypted with the wrong application key")
