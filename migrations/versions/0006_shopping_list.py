"""Список покупок: продукти сім'ї, відповідність інгредієнт → продукт, рядки списку

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _family() -> sa.Column:
    fk = sa.ForeignKey("families.id", ondelete="CASCADE")
    return sa.Column("family_id", sa.Integer, fk, nullable=False)


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer, primary_key=True),
        _family(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("aisle", sa.String(20), server_default="other", nullable=False),
        sa.Column("staple", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("skip", sa.Boolean, server_default=sa.false(), nullable=False),
        _created(),
    )
    op.create_index("uq_products_family_name", "products", ["family_id", "name"], unique=True)
    op.create_table(
        "ingredient_products",
        sa.Column(
            "family_id",
            sa.Integer,
            sa.ForeignKey("families.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("raw", sa.String(200), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer,
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_table(
        "shopping_items",
        sa.Column("id", sa.Integer, primary_key=True),
        _family(),
        sa.Column(
            "product_id",
            sa.Integer,
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("qty", sa.Float),
        sa.Column("unit", sa.String(30)),
        sa.Column("dish_id", sa.Integer, sa.ForeignKey("dishes.id", ondelete="CASCADE")),
        sa.Column("servings", sa.SmallInteger),
        sa.Column("added_by", sa.Integer, sa.ForeignKey("members.id", ondelete="SET NULL")),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        _created(),
    )
    op.create_index("ix_shopping_items_family", "shopping_items", ["family_id"])


def downgrade() -> None:
    op.drop_table("shopping_items")
    op.drop_table("ingredient_products")
    op.drop_table("products")
