"""persistent PDF annotations

Revision ID: e3f9c8a1d204
Revises: c814e9b03f2a
Create Date: 2026-10-05 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3f9c8a1d204"
down_revision: Union[str, None] = "c814e9b03f2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pdf_annotations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("geometry", sa.JSON(), nullable=False),
        sa.Column("color", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["user_documents.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pdf_annotations_document_id"),
        "pdf_annotations",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pdf_annotations_owner_id"),
        "pdf_annotations",
        ["owner_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pdf_annotations_owner_id"), table_name="pdf_annotations")
    op.drop_index(op.f("ix_pdf_annotations_document_id"), table_name="pdf_annotations")
    op.drop_table("pdf_annotations")
