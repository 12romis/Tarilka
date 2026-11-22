"""Довідники сім'ї (теги, члени) одним об'єктом: для промптів, карток і фільтрів."""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import PromptMember
from app.db.models import Member, Tag, TagKind

KIND_ORDER = [TagKind.meal, TagKind.course, TagKind.ingredient, TagKind.trait, TagKind.cuisine]


@dataclass
class FamilyMeta:
    tags: list[Tag]
    members: list[Member]
    by_slug: dict[tuple[str, str], Tag] = field(init=False)
    by_id: dict[int, Tag] = field(init=False)
    member_by_id: dict[int, Member] = field(init=False)

    def __post_init__(self) -> None:
        self.by_slug = {(t.kind.value, t.slug): t for t in self.tags}
        self.by_id = {t.id: t for t in self.tags}
        self.member_by_id = {m.id: m for m in self.members}

    def slugs(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for t in self.tags:
            out.setdefault(t.kind.value, set()).add(t.slug)
        return out

    def kind_tags(self, kind: str) -> list[Tag]:
        return [t for t in self.tags if t.kind.value == kind]

    def prompt_tags(self) -> dict[str, list[tuple[str, str]]]:
        return {k.value: [(t.slug, t.label) for t in self.kind_tags(k.value)] for k in KIND_ORDER}

    def prompt_members(self) -> list[PromptMember]:
        return [PromptMember(m.id, m.name, list(m.aliases)) for m in self.members]

    def tag(self, kind: str, slug: str | None) -> Tag | None:
        return self.by_slug.get((kind, slug)) if slug else None


async def load_meta(session: AsyncSession, family_id: int) -> FamilyMeta:
    tags = list(
        await session.scalars(
            select(Tag).where(Tag.family_id == family_id).order_by(Tag.kind, Tag.sort, Tag.id)
        )
    )
    members = list(
        await session.scalars(
            select(Member).where(Member.family_id == family_id).order_by(Member.id)
        )
    )
    return FamilyMeta(tags, members)
