"""Вхід у браузер/PWA: одноразові посилання й коди, сесії

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str, **kw) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def _member() -> sa.Column:
    fk = sa.ForeignKey("members.id", ondelete="CASCADE")
    return sa.Column("member_id", sa.Integer, fk, nullable=False)


def upgrade() -> None:
    op.create_table(
        "login_links",
        sa.Column("id", sa.Integer, primary_key=True),
        _member(),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("code", sa.String(6), nullable=False),
        _ts("expires_at", nullable=False),
        _ts("used_at"),
        _ts("created_at", server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "web_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        _member(),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_agent", sa.String(300)),
        _ts("created_at", server_default=sa.func.now(), nullable=False),
        _ts("last_seen_at", server_default=sa.func.now(), nullable=False),
        _ts("expires_at", nullable=False),
        _ts("revoked_at"),
    )
    op.create_index("ix_web_sessions_member", "web_sessions", ["member_id"])


def downgrade() -> None:
    op.drop_table("web_sessions")
    op.drop_table("login_links")
