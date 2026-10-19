"""persistent patent ordering

Revision ID: a72f2e9b61c4
Revises: e3f9c8a1d204
Create Date: 2026-10-19 13:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a72f2e9b61c4"
down_revision: str | None = "e3f9c8a1d204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_documents") as batch_op:
        batch_op.add_column(
            sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("user_documents") as batch_op:
        batch_op.drop_column("sort_order")
