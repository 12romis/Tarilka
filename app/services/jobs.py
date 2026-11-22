"""Черга import_jobs у Postgres: SELECT … FOR UPDATE SKIP LOCKED, без Redis."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ImportJob, JobKind, JobStatus

MAX_ATTEMPTS = 3  # перша спроба + 2 повтори


async def enqueue(
    session: AsyncSession,
    *,
    family_id: int,
    kind: JobKind,
    payload: dict[str, Any],
    created_by: int | None,
    tg_chat_id: int | None = None,
    tg_message_id: int | None = None,
) -> ImportJob:
    job = ImportJob(
        family_id=family_id,
        kind=kind,
        payload=payload,
        created_by=created_by,
        tg_chat_id=tg_chat_id,
        tg_message_id=tg_message_id,
    )
    session.add(job)
    await session.commit()
    return job


async def claim(session: AsyncSession) -> ImportJob | None:
    """Атомарно бере одну задачу з черги й переводить її в running."""
    job_id = await session.scalar(
        text(
            "UPDATE import_jobs SET status = 'running', attempts = attempts + 1, "
            "updated_at = now() "
            "WHERE id = (SELECT id FROM import_jobs WHERE status = 'queued' AND run_after <= now() "
            "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id"
        )
    )
    await session.commit()
    return await session.get(ImportJob, job_id) if job_id else None


async def requeue_stale(session: AsyncSession, older_than: timedelta) -> int:
    """Задачі, що «зависли» в running (worker перезапустився), повертаються в чергу."""
    res = await session.execute(
        update(ImportJob)
        .where(
            ImportJob.status == JobStatus.running,
            ImportJob.updated_at < datetime.now(UTC) - older_than,
        )
        .values(status=JobStatus.queued)
    )
    await session.commit()
    return res.rowcount or 0


async def complete(session: AsyncSession, job: ImportJob, result: dict[str, Any]) -> None:
    """Чернетки готові; якщо страв не знайшлося — задачу одразу закрито."""
    job.status = JobStatus.draft_ready if result.get("drafts") else JobStatus.done
    job.result = result
    job.error = None
    await session.commit()


async def fail(
    session: AsyncSession, job: ImportJob, error: str, retry_delays_s: list[int]
) -> bool:
    """Фіксує помилку. Повертає True, якщо задачу заплановано повторити."""
    job.error = error[:2000]
    if job.attempts < MAX_ATTEMPTS:
        delay = retry_delays_s[min(job.attempts - 1, len(retry_delays_s) - 1)]
        job.status = JobStatus.queued
        job.run_after = datetime.now(UTC) + timedelta(seconds=delay)
        await session.commit()
        return True
    job.status = JobStatus.failed
    await session.commit()
    return False


async def stale_drafts(session: AsyncSession, older_than: timedelta) -> list[ImportJob]:
    return list(
        await session.scalars(
            select(ImportJob).where(
                ImportJob.status == JobStatus.draft_ready,
                ImportJob.updated_at < datetime.now(UTC) - older_than,
            )
        )
    )
