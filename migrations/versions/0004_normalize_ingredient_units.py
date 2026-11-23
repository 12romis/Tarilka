"""Канонічні одиниці інгредієнтів у наявних стравах («ст.л» → «ст. л.» тощо)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.services.units import normalize_ingredients

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, ingredients FROM dishes WHERE jsonb_typeof(ingredients) = 'array'")
    )
    for dish_id, items in rows.all():
        fixed = normalize_ingredients(items)
        if fixed != items:
            conn.execute(
                sa.text("UPDATE dishes SET ingredients = CAST(:v AS jsonb) WHERE id = :id"),
                {"v": json.dumps(fixed, ensure_ascii=False) if fixed else None, "id": dish_id},
            )


def downgrade() -> None:
    """Вихідні написання не зберігаються — відкат лише схеми (її тут немає)."""
