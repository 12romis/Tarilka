"""Модель даних (див. план MVP, розділ «Модель даних»)."""

import enum
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class DishStatus(enum.StrEnum):
    can_cook = "can_cook"
    want_to_try = "want_to_try"
    archived = "archived"


class SourceType(enum.StrEnum):
    manual = "manual"
    web = "web"
    tiktok = "tiktok"
    instagram = "instagram"
    youtube = "youtube"
    photo = "photo"
    voice = "voice"


class TagKind(enum.StrEnum):
    meal = "meal"
    course = "course"
    ingredient = "ingredient"
    cuisine = "cuisine"
    trait = "trait"


class ImageOrigin(enum.StrEnum):
    og_image = "og_image"
    video_frame = "video_frame"
    upload = "upload"


class JobKind(enum.StrEnum):
    url = "url"
    text = "text"
    voice = "voice"
    photo = "photo"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    draft_ready = "draft_ready"
    failed = "failed"
    done = "done"


_now = {"server_default": func.now()}


class Family(Base):
    __tablename__ = "families"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_now)


class Member(Base):
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    # nullable — не всі члени сім'ї мають Telegram
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    # Username без «@» у нижньому регістрі: прив'язується до tg_user_id при першому повідомленні.
    tg_username: Mapped[str | None] = mapped_column(String(64), unique=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_now)


class Invite(Base):
    __tablename__ = "invites"
    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    # Ім'я, під яким новий член з'явиться в сім'ї (інакше — ім'я з Telegram).
    name: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_now)


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (Index("uq_tags_family_kind_slug", "family_id", "kind", "slug", unique=True),)
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    kind: Mapped[TagKind] = mapped_column(_enum(TagKind, "tag_kind"))
    slug: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(100))
    emoji: Mapped[str | None] = mapped_column(String(16))
    sort: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class Dish(Base):
    __tablename__ = "dishes"
    __table_args__ = (
        CheckConstraint("difficulty BETWEEN 1 AND 3", name="ck_dishes_difficulty"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_dishes_rating"),
        Index("ix_dishes_family_status", "family_id", "status"),
        Index(
            "ix_dishes_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "uq_dishes_family_source_url",
            "family_id",
            "source_url",
            unique=True,
            postgresql_where=text("source_url IS NOT NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[DishStatus] = mapped_column(
        _enum(DishStatus, "dish_status"), server_default=DishStatus.can_cook.value
    )
    main_ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("tags.id", ondelete="SET NULL")
    )
    cook_time_min: Mapped[int | None] = mapped_column(Integer)
    difficulty: Mapped[int | None] = mapped_column(SmallInteger)
    servings: Mapped[int | None] = mapped_column(SmallInteger)
    ingredients: Mapped[list | None] = mapped_column(JSONB)
    steps: Mapped[list | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[SourceType] = mapped_column(
        _enum(SourceType, "source_type"), server_default=SourceType.manual.value
    )
    source_author: Mapped[str | None] = mapped_column(String(200))
    raw_import: Mapped[dict | None] = mapped_column(JSONB)
    last_cooked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooked_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DishImage(Base):
    __tablename__ = "dish_images"
    id: Mapped[int] = mapped_column(primary_key=True)
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(Text)
    path_thumb: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    is_cover: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    origin: Mapped[ImageOrigin] = mapped_column(_enum(ImageOrigin, "image_origin"))


class DishCook(Base):
    __tablename__ = "dish_cooks"
    dish_id: Mapped[int] = mapped_column(
        ForeignKey("dishes.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class DishTag(Base):
    __tablename__ = "dish_tags"
    dish_id: Mapped[int] = mapped_column(
        ForeignKey("dishes.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class CookLog(Base):
    __tablename__ = "cook_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    cooked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_now)
    note: Mapped[str | None] = mapped_column(Text)


class ImportJob(Base):
    __tablename__ = "import_jobs"
    __table_args__ = (Index("ix_import_jobs_status_created", "status", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    kind: Mapped[JobKind] = mapped_column(_enum(JobKind, "job_kind"))
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), server_default=JobStatus.queued.value
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB)
    dish_id: Mapped[int | None] = mapped_column(ForeignKey("dishes.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
