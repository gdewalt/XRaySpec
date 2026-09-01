"""Audit trail helper (DESIGN.md §7, §18.3).

Records authorization-relevant and lifecycle actions. ``details`` is content-free
metadata only — never document text, selections, or other sensitive content.
The caller is responsible for committing the surrounding transaction.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuditEvent


def record_audit(
    session: AsyncSession,
    *,
    actor_user_id: str | None,
    action: str,
    resource_class: str,
    resource_id: str | None = None,
    outcome: str = "success",
    correlation_id: str | None = None,
    details: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_user_id=actor_user_id,
        action=action,
        resource_class=resource_class,
        resource_id=resource_id,
        outcome=outcome,
        correlation_id=correlation_id,
        details=details,
    )
    session.add(event)
    return event
