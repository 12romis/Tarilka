"""Чернетки страв живуть в import_jobs.result, а не в FSM.

result = {
  "drafts": [{...поля DishDraft після clean_draft, "state": pending|saved|discarded,
              "dish_id": int|None}],
  "source_text": str, "transcript": str|None, "view": "summary"|int,
  "rename": {"msg_id": int, "i": int}|None, "meta": {...}
}
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import Dish, ImportJob, JobKind, JobStatus, SourceType
from app.services import dishes as dish_svc
from app.services.meta import FamilyMeta

PENDING, SAVED, DISCARDED = "pending", "saved", "discarded"

SOURCE_BY_KIND = {JobKind.text: SourceType.manual, JobKind.voice: SourceType.voice}


def drafts(job: ImportJob) -> list[dict[str, Any]]:
    return (job.result or {}).get("drafts", [])


def touch(job: ImportJob) -> None:
    """JSONB змінено на місці — повідомити SQLAlchemy і закрити задачу, якщо все вирішено."""
    flag_modified(job, "result")
    if job.result and drafts(job) and all(d["state"] != PENDING for d in drafts(job)):
        job.status = JobStatus.done


def pending_indexes(job: ImportJob) -> list[int]:
    return [i for i, d in enumerate(drafts(job)) if d["state"] == PENDING]


def toggle_cook(draft: dict[str, Any], member_id: int) -> None:
    cooks = draft.setdefault("cooks", [])
    if member_id in cooks:
        cooks.remove(member_id)
    else:
        cooks.append(member_id)


def toggle_status(draft: dict[str, Any]) -> None:
    draft["status"] = "want_to_try" if draft.get("status") == "can_cook" else "can_cook"


def selected_slugs(draft: dict[str, Any], kind: str) -> list[str]:
    if kind == "ingredient":
        main = draft.get("main_ingredient")
        return ([main] if main else []) + list(draft.get("ingredient_tags") or [])
    if kind == "cuisine":
        return [draft["cuisine"]] if draft.get("cuisine") else []
    return list(draft.get({"meal": "meal", "course": "course", "trait": "traits"}[kind]) or [])


def toggle_tag(draft: dict[str, Any], kind: str, slug: str) -> None:
    """Перемикає тег. Для продуктів перший обраний — основний (main_ingredient)."""
    if kind == "ingredient":
        main = draft.get("main_ingredient")
        extra = list(draft.get("ingredient_tags") or [])
        if slug == main:
            main = extra.pop(0) if extra else None
        elif slug in extra:
            extra.remove(slug)
        elif main is None:
            main = slug
        else:
            extra.append(slug)
        draft["main_ingredient"], draft["ingredient_tags"] = main, extra
        return
    if kind == "cuisine":
        draft["cuisine"] = None if draft.get("cuisine") == slug else slug
        return
    key = {"meal": "meal", "course": "course", "trait": "traits"}[kind]
    values = list(draft.get(key) or [])
    values.remove(slug) if slug in values else values.append(slug)
    draft[key] = values


async def save_one(
    session: AsyncSession, meta: FamilyMeta, job: ImportJob, i: int, *, commit: bool = True
) -> Dish | None:
    d = drafts(job)[i]
    if d["state"] != PENDING:
        return None
    dish = await dish_svc.create_from_draft(
        session,
        meta,
        job.family_id,
        d,
        created_by=job.created_by,
        source_type=SOURCE_BY_KIND.get(job.kind, SourceType.manual),
        raw_import={"job_id": job.id, "source_text": (job.result or {}).get("source_text")},
        commit=False,
    )
    d["state"], d["dish_id"] = SAVED, dish.id
    touch(job)
    if commit:
        await session.commit()
    return dish


async def save_all(session: AsyncSession, meta: FamilyMeta, job: ImportJob) -> int:
    n = 0
    for i in pending_indexes(job):
        if await save_one(session, meta, job, i, commit=False):
            n += 1
    await session.commit()
    return n


async def discard(session: AsyncSession, job: ImportJob, i: int | None) -> None:
    for idx, d in enumerate(drafts(job)):
        if (i is None or idx == i) and d["state"] == PENDING:
            d["state"] = DISCARDED
    touch(job)
    if not pending_indexes(job):
        job.status = JobStatus.done
    await session.commit()


async def find_rename_job(session: AsyncSession, chat_id: int, msg_id: int) -> ImportJob | None:
    return await session.scalar(
        select(ImportJob).where(
            ImportJob.tg_chat_id == chat_id,
            ImportJob.result["rename"]["msg_id"].as_integer() == msg_id,
        )
    )
