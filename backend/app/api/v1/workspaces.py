"""Owner-scoped workspaces and patent organization."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update

from ...db.models import UserDocument, Workspace
from ...schemas.documents import DocumentRead
from ...schemas.workspaces import (
    DocumentPlacementUpdate,
    DocumentWorkspaceUpdate,
    WorkspaceCreate,
    WorkspaceList,
    WorkspaceRead,
    WorkspaceUpdate,
)
from ...services.audit import record_audit
from ..deps import CurrentUser, DbSession
from .documents import _owned_document

router = APIRouter(tags=["workspaces"])


async def _owned_workspace(session, user, workspace_id: str) -> Workspace:
    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.owner_id == user.id,
        )
    )
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    return workspace


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


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdate,
    user: CurrentUser,
    session: DbSession,
) -> WorkspaceRead:
    workspace = await _owned_workspace(session, user, workspace_id)
    duplicate = await session.scalar(
        select(Workspace).where(
            Workspace.owner_id == user.id,
            Workspace.name == body.name,
            Workspace.id != workspace_id,
        )
    )
    if duplicate is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A workspace with this name already exists")

    workspace.name = body.name
    record_audit(
        session,
        actor_user_id=user.id,
        action="workspace.update",
        resource_class="workspace",
        resource_id=workspace.id,
    )
    await session.commit()
    await session.refresh(workspace)
    return WorkspaceRead.model_validate(workspace)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str,
    user: CurrentUser,
    session: DbSession,
) -> None:
    workspace = await _owned_workspace(session, user, workspace_id)
    await session.execute(
        update(UserDocument)
        .where(
            UserDocument.owner_id == user.id,
            UserDocument.workspace_id == workspace.id,
        )
        .values(workspace_id=None)
    )
    await session.delete(workspace)
    record_audit(
        session,
        actor_user_id=user.id,
        action="workspace.delete",
        resource_class="workspace",
        resource_id=workspace.id,
    )
    await session.commit()


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


def _workspace_filter(workspace_id: str | None):
    return (
        UserDocument.workspace_id.is_(None)
        if workspace_id is None
        else UserDocument.workspace_id == workspace_id
    )


@router.patch(
    "/documents/{document_id}/placement",
    response_model=DocumentRead,
)
async def place_document(
    document_id: str,
    body: DocumentPlacementUpdate,
    user: CurrentUser,
    session: DbSession,
) -> DocumentRead:
    """Move a patent and place it before another patent in the target workspace."""
    document = await _owned_document(session, user, document_id)
    source_workspace_id = document.workspace_id

    if body.workspace_id is not None:
        await _owned_workspace(session, user, body.workspace_id)

    target_documents = list(
        await session.scalars(
            select(UserDocument)
            .where(
                UserDocument.owner_id == user.id,
                UserDocument.state != "deleted",
                _workspace_filter(body.workspace_id),
                UserDocument.id != document.id,
            )
            .order_by(UserDocument.sort_order.asc(), UserDocument.created_at.desc())
        )
    )
    target_ids = [item.id for item in target_documents]
    if body.before_document_id is not None and body.before_document_id not in target_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid target position")

    insertion_index = (
        target_ids.index(body.before_document_id)
        if body.before_document_id is not None
        else len(target_documents)
    )
    target_documents.insert(insertion_index, document)
    document.workspace_id = body.workspace_id
    for position, item in enumerate(target_documents):
        item.sort_order = position

    if source_workspace_id != body.workspace_id:
        source_documents = await session.scalars(
            select(UserDocument)
            .where(
                UserDocument.owner_id == user.id,
                UserDocument.state != "deleted",
                _workspace_filter(source_workspace_id),
                UserDocument.id != document.id,
            )
            .order_by(UserDocument.sort_order.asc(), UserDocument.created_at.desc())
        )
        for position, item in enumerate(source_documents):
            item.sort_order = position

    record_audit(
        session,
        actor_user_id=user.id,
        action="document.place",
        resource_class="user_document",
        resource_id=document.id,
        details={
            "workspace_id": body.workspace_id,
            "before_document_id": body.before_document_id,
        },
    )
    await session.commit()
    await session.refresh(document)
    return DocumentRead.model_validate(document)
