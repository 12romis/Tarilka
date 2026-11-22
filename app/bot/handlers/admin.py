"""Команди адміна: /export."""

import json
from datetime import UTC, datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.config import get_settings
from app.db.models import Member
from app.services.dishes import export_family
from app.services.members import is_admin


async def cmd_export(message: Message, session: AsyncSession, member: Member) -> None:
    if not is_admin(member, get_settings().admin_tg_ids):
        await message.answer(texts.ADMIN_ONLY)
        return
    data = await export_family(session, member.family_id)
    raw = json.dumps(data, ensure_ascii=False, indent=1, default=str).encode()
    name = f"tarilka-{datetime.now(UTC):%Y%m%d-%H%M}.json"
    await message.answer_document(
        BufferedInputFile(raw, filename=name),
        caption=texts.EXPORT_CAPTION.format(
            dishes=len(data["dishes"]), members=len(data["members"])
        ),
    )


def build_router() -> Router:
    router = Router(name="admin")
    router.message.register(cmd_export, Command("export"))
    return router
