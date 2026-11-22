"""Чернетки страв живуть в import_jobs.result, а не в FSM.

result = {
  "drafts": [{...поля чернетки (див. draft_fields), "state": pending|saved|discarded,
              "dish_id": int|None}],
  "source_text": str, "source_type": SourceType, "transcript": str|None,
  "view": "summary"|int, "rename": {"msg_id": int, "i": int}|None, "meta": {...}
}
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import Dish, ImportJob, JobStatus, Member, SourceType
from app.services import dishes as dish_svc
from app.services.draft_fields import Draft, raw_text_draft
from app.services.meta import FamilyMeta

PENDING, SAVED, DISCARDED = "pending", "saved", "discarded"
SUMMARY = "summary"


def new_result(
    drafts: list[Draft],
    *,
    source_text: str,
    source_type: SourceType,
    transcript: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "drafts": [d | {"state": PENDING, "dish_id": None} for d in drafts],
        "source_text": source_text,
        "source_type": source_type.value,
        "transcript": transcript,
        "view": SUMMARY if len(drafts) > 1 else 0,
        "rename": None,
        "meta": meta or {},
    }


def drafts(job: ImportJob) -> list[Draft]:
    return (job.result or {}).get("drafts", [])


def touch(job: ImportJob) -> None:
    """JSONB змінено на місці — повідомити SQLAlchemy і закрити задачу, якщо все вирішено."""
    flag_modified(job, "result")
    if drafts(job) and not pending_indexes(job):
        job.status = JobStatus.done


def pending_indexes(job: ImportJob) -> list[int]:
    return [i for i, d in enumerate(drafts(job)) if d["state"] == PENDING]


def next_pending(job: ImportJob, after: int) -> int | str:
    """Наступна непідтверджена чернетка після `after` (по колу), або зведена картка."""
    pending = pending_indexes(job)
    later = [p for p in pending if p > after]
    return (later or pending or [SUMMARY])[0]


def set_view(job: ImportJob, view: int | str) -> None:
    job.result["view"] = view
    touch(job)


async def save_one(
    session: AsyncSession, meta: FamilyMeta, job: ImportJob, i: int, *, commit: bool = True
) -> Dish | None:
    d = drafts(job)[i]
    if d["state"] != PENDING:
        return None
    result = job.result or {}
    dish = await dish_svc.create_from_draft(
        session,
        meta,
        job.family_id,
        d,
        created_by=job.created_by,
        source_type=SourceType(result.get("source_type", SourceType.manual)),
        raw_import={"job_id": job.id, "source_text": result.get("source_text")},
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


async def save_raw_text(
    session: AsyncSession, meta: FamilyMeta, job: ImportJob, member: Member
) -> Dish:
    """«Зберегти як є» для задачі, яку не вдалося розібрати."""
    dish = await dish_svc.create_from_draft(
        session,
        meta,
        job.family_id,
        raw_text_draft((job.payload or {}).get("text") or "", member.id),
        created_by=member.id,
        commit=False,
    )
    job.status, job.dish_id = JobStatus.done, dish.id
    await session.commit()
    return dish


async def discard(session: AsyncSession, job: ImportJob, i: int | None) -> None:
    for idx, d in enumerate(drafts(job)):
        if (i is None or idx == i) and d["state"] == PENDING:
            d["state"] = DISCARDED
    touch(job)
    await session.commit()


async def find_rename_job(session: AsyncSession, chat_id: int, msg_id: int) -> ImportJob | None:
    return await session.scalar(
        select(ImportJob).where(
            ImportJob.tg_chat_id == chat_id,
            ImportJob.result["rename"]["msg_id"].as_integer() == msg_id,
        )
    )
