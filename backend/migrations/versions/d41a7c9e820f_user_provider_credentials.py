"""user provider credentials

Revision ID: d41a7c9e820f
Revises: b16c2a7d9e31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d41a7c9e820f"
down_revision: str | None = "b16c2a7d9e31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("uspto_api_key_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "uspto_api_key_encrypted")
