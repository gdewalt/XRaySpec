"""Owner-scoped account settings. Secret values are never returned."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from ...config import get_settings
from ...db.models import AuditEvent
from ...schemas.account import AccountRead, AiUsageRead, UsptoApiKeyUpdate
from ...services.audit import record_audit
from ...services.credentials import CredentialEncryptionError, encrypt_credential
from ..deps import CurrentUser, DbSession

router = APIRouter(tags=["account"])


async def _account_read(user, session) -> AccountRead:
    events = list(
        await session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.actor_user_id == user.id,
                AuditEvent.action.in_(("ai.scan.usage", "ai.scan.complete")),
            )
            .order_by(AuditEvent.created_at.desc())
        )
    )
    usage_events = [event for event in events if event.action == "ai.scan.usage"]
    scan_events = [event for event in events if event.action == "ai.scan.complete"]
    usage = AiUsageRead(
        requests=len(usage_events),
        scans=len(scan_events),
        input_tokens=sum(
            int((event.details or {}).get("input_tokens", 0)) for event in usage_events
        ),
        output_tokens=sum(
            int((event.details or {}).get("output_tokens", 0)) for event in usage_events
        ),
        total_tokens=sum(
            int((event.details or {}).get("total_tokens", 0)) for event in usage_events
        ),
        last_used_at=usage_events[0].created_at if usage_events else None,
    )
    settings = get_settings()
    return AccountRead(
        email=user.email,
        uspto_api_key_configured=bool(user.uspto_api_key_encrypted),
        ai_scan_available=settings.ai_scan_enabled and bool(settings.openai_api_key),
        ai_usage=usage,
    )


@router.get("/account", response_model=AccountRead)
async def get_account(user: CurrentUser, session: DbSession) -> AccountRead:
    return await _account_read(user, session)


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
    return await _account_read(user, session)


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
