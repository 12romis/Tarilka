"""Спільний інтерфейс обробників import_jobs.

Кожен тип задачі (text, voice, далі — url, photo) — окремий Importer, зареєстрований
у `app.importers.registry`. Worker не знає подробиць: дає контекст, отримує ImportOutput.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient
from app.config import Settings
from app.db.models import ImportJob, SourceType
from app.services.meta import FamilyMeta


class PermanentImportError(Exception):
    """Помилка, яку немає сенсу повторювати; текст — людська причина для чату."""


class DuplicateUrl(Exception):
    """Посилання вже є в каталозі — показуємо наявну страву замість нової чернетки."""

    def __init__(self, dish_id: int) -> None:
        super().__init__(dish_id)
        self.dish_id = dish_id


@dataclass
class RawExtract:
    """Те, що вдалося дістати з джерела, до нормалізації LLM."""

    url: str
    source_type: SourceType
    title: str | None = None
    text_blocks: list[str] = field(default_factory=list)  # підпис, опис, текст сторінки
    structured: dict[str, Any] | None = None  # schema.org Recipe (назва, інгредієнти, кроки)
    image_urls: list[str] = field(default_factory=list)
    source_author: str | None = None
    via: str = ""  # хто дістав: web / ytdlp / oembed / link

    @property
    def has_content(self) -> bool:
        return bool(self.title or self.structured or any(b.strip() for b in self.text_blocks))


@dataclass
class ImportContext:
    ai: AIClient
    bot: Bot
    meta: FamilyMeta
    tmp_dir: Path  # власна тимчасова тека задачі; worker видаляє її після обробки
    session: AsyncSession
    http: httpx.AsyncClient
    settings: Settings


@dataclass
class ImportOutput:
    drafts: list[dict[str, Any]]
    source_text: str
    source_type: SourceType = SourceType.manual
    transcript: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)  # модель, токени, час


class Importer(Protocol):
    async def run(self, job: ImportJob, ctx: ImportContext) -> ImportOutput: ...
