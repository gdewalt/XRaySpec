"""Owner-scoped account settings. Secret values are never returned."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from ...config import get_settings
from ...schemas.account import AccountRead, UsptoApiKeyUpdate
from ...services.audit import record_audit
from ...services.credentials import CredentialEncryptionError, encrypt_credential
from ..deps import CurrentUser, DbSession

router = APIRouter(tags=["account"])


@router.get("/account", response_model=AccountRead)
async def get_account(user: CurrentUser) -> AccountRead:
    return AccountRead(
        email=user.email,
        uspto_api_key_configured=bool(user.uspto_api_key_encrypted),
    )


@router.put("/account/uspto-api-key", response_model=AccountRead)
async def set_uspto_api_key(
    body: UsptoApiKeyUpdate,
    user: CurrentUser,
    session: DbSession,
) -> AccountRead:
    try:
        user.uspto_api_key_encrypted = encrypt_credential(
            body.api_key,
            get_settings().credential_encryption_key,
        )
    except CredentialEncryptionError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    record_audit(
        session,
        actor_user_id=user.id,
        action="account.uspto_api_key.set",
        resource_class="user",
        resource_id=user.id,
    )
    await session.commit()
    return AccountRead(email=user.email, uspto_api_key_configured=True)


@router.delete("/account/uspto-api-key", status_code=status.HTTP_204_NO_CONTENT)
async def delete_uspto_api_key(user: CurrentUser, session: DbSession) -> Response:
    user.uspto_api_key_encrypted = None
    record_audit(
        session,
        actor_user_id=user.id,
        action="account.uspto_api_key.delete",
        resource_class="user",
        resource_id=user.id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
