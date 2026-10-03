"""Owner-scoped workspaces and patent organization."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ...db.models import Workspace
from ...schemas.documents import DocumentRead
from ...schemas.workspaces import (
    DocumentWorkspaceUpdate,
    WorkspaceCreate,
    WorkspaceList,
    WorkspaceRead,
)
from ...services.audit import record_audit
from ..deps import CurrentUser, DbSession
from .documents import _owned_document

router = APIRouter(tags=["workspaces"])


@router.get("/workspaces", response_model=WorkspaceList)
async def list_workspaces(user: CurrentUser, session: DbSession) -> WorkspaceList:
    rows = await session.scalars(
        select(Workspace)
        .where(Workspace.owner_id == user.id)
        .order_by(Workspace.name.asc(), Workspace.created_at.asc())
    )
    return WorkspaceList(items=[WorkspaceRead.model_validate(row) for row in rows])


@router.post(
    "/workspaces",
    response_model=WorkspaceRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    body: WorkspaceCreate, user: CurrentUser, session: DbSession
) -> WorkspaceRead:
    existing = await session.scalar(
        select(Workspace).where(
            Workspace.owner_id == user.id,
            Workspace.name == body.name,
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A workspace with this name already exists")

    workspace = Workspace(owner_id=user.id, name=body.name)
    session.add(workspace)
    await session.flush()
    record_audit(
        session,
        actor_user_id=user.id,
        action="workspace.create",
        resource_class="workspace",
        resource_id=workspace.id,
    )
    await session.commit()
    await session.refresh(workspace)
    return WorkspaceRead.model_validate(workspace)


@router.patch(
    "/documents/{document_id}/workspace",
    response_model=DocumentRead,
)
async def move_document_to_workspace(
    document_id: str,
    body: DocumentWorkspaceUpdate,
    user: CurrentUser,
    session: DbSession,
) -> DocumentRead:
    document = await _owned_document(session, user, document_id)

    if body.workspace_id is not None:
        workspace = await session.scalar(
            select(Workspace).where(
                Workspace.id == body.workspace_id,
                Workspace.owner_id == user.id,
            )
        )
        if workspace is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")

    document.workspace_id = body.workspace_id
    record_audit(
        session,
        actor_user_id=user.id,
        action="document.move",
        resource_class="user_document",
        resource_id=document.id,
        details={"workspace_id": body.workspace_id},
    )
    await session.commit()
    await session.refresh(document)
    return DocumentRead.model_validate(document)
