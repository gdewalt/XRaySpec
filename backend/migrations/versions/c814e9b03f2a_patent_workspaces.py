"""patent workspaces

Revision ID: c814e9b03f2a
Revises: 6b94b5f62a11
Create Date: 2026-10-02 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c814e9b03f2a"
down_revision: Union[str, None] = "6b94b5f62a11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name", name="uq_workspace_owner_name"),
    )
    op.create_index(
        op.f("ix_workspaces_owner_id"),
        "workspaces",
        ["owner_id"],
        unique=False,
    )

    with op.batch_alter_table("user_documents") as batch_op:
        batch_op.add_column(sa.Column("workspace_id", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_user_documents_workspace_id_workspaces",
            "workspaces",
            ["workspace_id"],
            ["id"],
        )
        batch_op.create_index(
            op.f("ix_user_documents_workspace_id"),
            ["workspace_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("user_documents") as batch_op:
        batch_op.drop_index(op.f("ix_user_documents_workspace_id"))
        batch_op.drop_constraint(
            "fk_user_documents_workspace_id_workspaces",
            type_="foreignkey",
        )
        batch_op.drop_column("workspace_id")

    op.drop_index(op.f("ix_workspaces_owner_id"), table_name="workspaces")
    op.drop_table("workspaces")
