"""Спільний інтерфейс обробників import_jobs.

Кожен тип задачі (text, voice, далі — url, photo) — окремий Importer, зареєстрований
у `app.importers.registry`. Worker не знає подробиць: дає контекст, отримує ImportOutput.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from aiogram import Bot

from app.ai.client import AIClient
from app.db.models import ImportJob, SourceType
from app.services.meta import FamilyMeta


class PermanentImportError(Exception):
    """Помилка, яку немає сенсу повторювати; текст — людська причина для чату."""


@dataclass
class ImportContext:
    ai: AIClient
    bot: Bot
    meta: FamilyMeta
    tmp_dir: Path  # власна тимчасова тека задачі; worker видаляє її після обробки


@dataclass
class ImportOutput:
    drafts: list[dict[str, Any]]
    source_text: str
    source_type: SourceType = SourceType.manual
    transcript: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)  # модель, токени, час


class Importer(Protocol):
    async def run(self, job: ImportJob, ctx: ImportContext) -> ImportOutput: ...
