"""seed: сім'я та довідники тегів

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FAMILY_ID = 1

# kind -> [(slug, label, emoji)]; порядок у списку = sort
TAGS: dict[str, list[tuple[str, str, str | None]]] = {
    "meal": [
        ("breakfast", "сніданок", "🌅"),
        ("lunch", "обід", "☀️"),
        ("dinner", "вечеря", "🌙"),
        ("snack", "перекус", "🍪"),
    ],
    "course": [
        ("soup", "суп", "🍲"),
        ("salad", "салат", "🥗"),
        ("main", "основна страва", "🍽"),
        ("side", "гарнір", "🍚"),
        ("baking", "випічка", "🥐"),
        ("dessert", "десерт", "🍰"),
        ("sauce", "соус", "🥫"),
        ("drink", "напій", "🥤"),
        ("preserve", "заготовка", "🫙"),
    ],
    "ingredient": [
        ("chicken", "курка", "🍗"),
        ("pork", "свинина", "🥓"),
        ("beef", "яловичина", "🥩"),
        ("minced", "фарш", "🍖"),
        ("fish", "риба", "🐟"),
        ("seafood", "морепродукти", "🦐"),
        ("eggs", "яйця", "🥚"),
        ("cheese", "сир і творог", "🧀"),
        ("potato", "картопля", "🥔"),
        ("grains", "крупи", "🌾"),
        ("pasta", "макарони", "🍝"),
        ("legumes", "бобові", "🫘"),
        ("vegetables", "овочі", "🥦"),
        ("mushrooms", "гриби", "🍄"),
        ("flour", "борошно", "🌾"),
    ],
    "trait": [
        ("quick", "швидко (≤ 30 хв)", "⚡"),
        ("festive", "на свято", "🎉"),
        ("kids", "для дітей", "🧒"),
        ("cheap", "дешево", "💸"),
        ("lenten", "пісне", "🌱"),
        ("oven", "в духовці", "♨️"),
        ("freezable", "можна заморозити", "❄️"),
    ],
    "cuisine": [
        ("ukrainian", "українська", "🇺🇦"),
        ("italian", "італійська", "🇮🇹"),
        ("asian", "азійська", "🥢"),
        ("georgian", "грузинська", "🇬🇪"),
        ("other", "інша", "🌍"),
    ],
}


def upgrade() -> None:
    op.execute(
        sa.text("INSERT INTO families (id, name) VALUES (:id, :name)").bindparams(
            id=FAMILY_ID, name="Наша сім'я"
        )
    )
    op.execute("SELECT setval('families_id_seq', (SELECT max(id) FROM families))")
    rows = [
        {
            "family_id": FAMILY_ID,
            "kind": kind,
            "slug": slug,
            "label": label,
            "emoji": emoji,
            "sort": i,
        }
        for kind, items in TAGS.items()
        for i, (slug, label, emoji) in enumerate(items)
    ]
    op.get_bind().execute(
        sa.text(
            "INSERT INTO tags (family_id, kind, slug, label, emoji, sort) "
            "VALUES (:family_id, CAST(:kind AS tag_kind), :slug, :label, :emoji, :sort)"
        ),
        rows,
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM tags WHERE family_id = :id").bindparams(id=FAMILY_ID))
    op.execute(sa.text("DELETE FROM families WHERE id = :id").bindparams(id=FAMILY_ID))
