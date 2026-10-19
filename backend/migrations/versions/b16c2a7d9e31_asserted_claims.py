"""persist asserted claims

Revision ID: b16c2a7d9e31
Revises: a72f2e9b61c4
Create Date: 2026-10-19 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b16c2a7d9e31"
down_revision: str | None = "a72f2e9b61c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asserted_claims",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("entry_id", sa.String(), nullable=False),
        sa.Column("claim_number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["user_documents.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "entry_id", name="uq_asserted_claim_entry"
        ),
    )
    op.create_index(
        op.f("ix_asserted_claims_document_id"),
        "asserted_claims",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_asserted_claims_owner_id"),
        "asserted_claims",
        ["owner_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_asserted_claims_owner_id"), table_name="asserted_claims")
    op.drop_index(op.f("ix_asserted_claims_document_id"), table_name="asserted_claims")
    op.drop_table("asserted_claims")
