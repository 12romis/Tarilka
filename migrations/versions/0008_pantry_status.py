"""Холодильник: статус «закінчилось» замість видалення

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pantry_items", sa.Column("out_at", sa.DateTime(timezone=True)))
    op.add_column("pantry_items", sa.Column("restored_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.execute("DELETE FROM pantry_items WHERE out_at IS NOT NULL")
    op.drop_column("pantry_items", "restored_at")
    op.drop_column("pantry_items", "out_at")
