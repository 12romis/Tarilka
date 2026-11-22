"""Worker: обробляє import_jobs через імпортери з реєстру, автозберігає чернетки за 48 год.

Запуск: python -m app.worker. Відповіді в чат надсилає напряму через Bot API.
"""

import asyncio
import logging
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import texts
from app.ai.client import AIClient, AIError, build_ai_client
from app.bot.cards import render_job
from app.bot.keyboards import DraftCb
from app.bot.router import build_bot
from app.bot.util import safe_edit
from app.config import Settings, get_settings
from app.db.models import ImportJob, JobKind, JobStatus, Member
from app.db.session import dispose_engine, get_sessionmaker
from app.importers.base import ImportContext, PermanentImportError
from app.importers.registry import IMPORTERS
from app.services import drafts as dr
from app.services import jobs
from app.services.meta import load_meta

log = logging.getLogger("tarilka.worker")


def human_reason(exc: BaseException) -> str:
    if isinstance(exc, PermanentImportError):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return texts.REASON_TIMEOUT
    if isinstance(exc, AIError):
        return texts.REASON_AI
    return texts.REASON_UNKNOWN


class Worker:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        bot: Bot,
        ai: AIClient,
        settings: Settings,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.bot = bot
        self.ai = ai
        self.settings = settings
        self.sem = asyncio.Semaphore(settings.worker_concurrency)
        self.tasks: set[asyncio.Task[None]] = set()

    async def _import(self, session: AsyncSession, job: ImportJob) -> dict[str, Any]:
        importer = IMPORTERS.get(job.kind)
        if importer is None:
            raise PermanentImportError(texts.REASON_UNKNOWN)
        tmp = Path(tempfile.mkdtemp(prefix=f"imp-{job.id}-"))
        try:
            ctx = ImportContext(self.ai, self.bot, await load_meta(session, job.family_id), tmp)
            out = await importer.run(job, ctx)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return dr.new_result(
            out.drafts,
            source_text=out.source_text,
            source_type=out.source_type,
            transcript=out.transcript,
            meta=out.meta,
        )

    # --- життєвий цикл задачі ------------------------------------------------------------

    async def process(self, job_id: int) -> None:
        async with self.sessionmaker() as session:
            job = await session.get(ImportJob, job_id)
            if job is None:
                return
            try:
                result = await asyncio.wait_for(
                    self._import(session, job), timeout=self.settings.job_timeout_s
                )
            except Exception as exc:
                await session.rollback()
                await self._on_error(session, job_id, exc)
                return
            job.result = result
            await dr.mark_existing(session, job)
            await jobs.complete(session, job, job.result)
            if not result["drafts"]:
                await self._edit(job, texts.NO_DISHES, None)
                return
            meta = await load_meta(session, job.family_id)
            text, markup = render_job(meta, job)
            await self._edit(job, text, markup)
            log.info("job %s: чернеток %s", job.id, len(result["drafts"]))

    async def _on_error(self, session: AsyncSession, job_id: int, exc: BaseException) -> None:
        job = await session.get(ImportJob, job_id)  # після rollback об'єкт перечитується
        assert job is not None
        log.warning("job %s: спроба %s — %r", job.id, job.attempts, exc)
        if isinstance(exc, PermanentImportError):
            job.attempts = jobs.MAX_ATTEMPTS
        retry = await jobs.fail(session, job, repr(exc), self.settings.job_retry_delays_s)
        if retry:
            await self._edit(job, texts.PARSE_RETRYING, None)
            return
        kb = None
        if job.kind == JobKind.text:
            b = InlineKeyboardBuilder()
            b.button(text=texts.BTN_SAVE_AS_IS, callback_data=DraftCb(job=job.id, i=-1, a="asis"))
            kb = b.as_markup()
        await self._edit(job, texts.PARSE_FAILED.format(reason=human_reason(exc)), kb)
        if not isinstance(exc, PermanentImportError):
            await self._notify_admins(session, job)

    async def _notify_admins(self, session: AsyncSession, job: ImportJob) -> None:
        ids = set(self.settings.admin_tg_ids)
        ids |= set(
            await session.scalars(
                select(Member.tg_user_id).where(
                    Member.family_id == job.family_id,
                    Member.is_admin.is_(True),
                    Member.tg_user_id.is_not(None),
                )
            )
        )
        text = texts.ADMIN_JOB_FAILED.format(
            job=job.id, kind=job.kind.value, n=job.attempts, error=(job.error or "")[:300]
        )
        for tg_id in ids:
            try:
                await self.bot.send_message(tg_id, text)
            except Exception:
                log.exception("не вдалося сповістити адміна %s", tg_id)

    async def _edit(self, job: ImportJob, text: str, markup: Any) -> None:
        if not (job.tg_chat_id and job.tg_message_id):
            return
        try:
            await safe_edit(self.bot, job.tg_chat_id, job.tg_message_id, text, markup)
        except Exception:
            log.exception("job %s: не вдалося оновити повідомлення", job.id)

    # --- автозбереження ------------------------------------------------------------------

    async def autosave(self) -> int:
        """Непідтверджені за 48 год чернетки зберігаються з тим статусом, що в чернетці."""
        n = 0
        async with self.sessionmaker() as session:
            stale = await jobs.stale_drafts(
                session, timedelta(hours=self.settings.draft_autosave_hours)
            )
            for job in stale:
                meta = await load_meta(session, job.family_id)
                n += await dr.save_all(session, meta, job)
                job.status = JobStatus.done
                await session.commit()
                await self._edit(job, render_job(meta, job)[0] + texts.AUTOSAVED, None)
        return n

    # --- головний цикл -------------------------------------------------------------------

    async def _run_one(self, job_id: int) -> None:
        try:
            await self.process(job_id)
        finally:
            self.sem.release()

    async def tick(self) -> int:
        """Бере з черги стільки задач, скільки дозволяє WORKER_CONCURRENCY."""
        started = 0
        while not self.sem.locked():
            await self.sem.acquire()
            async with self.sessionmaker() as session:
                job = await jobs.claim(session)
            if job is None:
                self.sem.release()
                break
            task = asyncio.create_task(self._run_one(job.id))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            started += 1
        return started

    async def run(self) -> None:
        async with self.sessionmaker() as session:
            n = await jobs.requeue_stale(
                session, timedelta(seconds=self.settings.job_timeout_s * 2)
            )
            if n:
                log.info("повернуто в чергу завислих задач: %s", n)
        last_autosave = 0.0
        loop = asyncio.get_running_loop()
        while True:
            try:
                await self.tick()
                if loop.time() - last_autosave > 60:
                    last_autosave = loop.time()
                    if saved := await self.autosave():
                        log.info("автозбережено чернеток: %s", saved)
            except Exception:
                log.exception("помилка циклу worker")
            await asyncio.sleep(self.settings.worker_poll_s)


async def main() -> None:
    settings = get_settings()
    bot = build_bot(settings)
    worker = Worker(get_sessionmaker(), bot, build_ai_client(settings), settings)
    log.info(
        "worker запущено, concurrency=%s, модель=%s",
        settings.worker_concurrency,
        settings.llm_model,
    )
    try:
        await worker.run()
    finally:
        await bot.session.close()
        await dispose_engine()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(main())
