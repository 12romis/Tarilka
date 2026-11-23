from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.config import get_settings
from app.db.models import Member
from app.services.members import (
    INVITE_PREFIX,
    InviteResult,
    TgUser,
    accept_invite,
    create_invite,
    is_admin,
)
from app.services.web_auth import create_login_link, login_url

WEB_START_ARG = "web"


async def start_invite(
    message: Message, command: CommandObject, session: AsyncSession, member: Member | None
) -> None:
    user = message.from_user
    assert user is not None
    token = (command.args or "").removeprefix(INVITE_PREFIX)
    result, joined = await accept_invite(
        session, token, TgUser(user.id, user.username, user.first_name)
    )
    if result is InviteResult.invalid or joined is None:
        await message.answer(texts.INVITE_INVALID)
        return
    prefix = texts.START_JOINED.format(name=joined.name) if result is InviteResult.joined else ""
    await message.answer(prefix + texts.START.format(name=joined.name))


async def start(message: Message, member: Member | None) -> None:
    if member is None:  # сюди можна потрапити лише з недійсним inv_-посиланням
        await message.answer(texts.INVITE_INVALID)
        return
    await message.answer(texts.START.format(name=member.name))


async def invite(
    message: Message, command: CommandObject, bot: Bot, session: AsyncSession, member: Member
) -> None:
    settings = get_settings()
    if not is_admin(member, settings.admin_tg_ids):
        await message.answer(texts.INVITE_ADMIN_ONLY)
        return
    inv = await create_invite(session, member, command.args, settings.invite_ttl_hours)
    me = await bot.me()
    link = f"https://t.me/{me.username}?start={INVITE_PREFIX}{inv.token}"
    for_name = texts.INVITE_FOR_NAME.format(name=inv.name) if inv.name else ""
    await message.answer(
        texts.INVITE_CREATED.format(for_name=for_name, hours=settings.invite_ttl_hours, link=link)
    )


async def help_(message: Message, bot: Bot) -> None:
    me = await bot.me()
    await message.answer(
        texts.HELP.format(bot_username=me.username),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def web_login(message: Message, session: AsyncSession, member: Member) -> None:
    """Одноразове посилання й код для входу в браузер/PWA (лише в особистому чаті)."""
    if message.chat.type != ChatType.PRIVATE:
        await message.answer(texts.WEB_LOGIN_PRIVATE_ONLY)
        return
    token, code, _ = await create_login_link(session, member)
    url = login_url(get_settings().public_base_url, token)
    await message.answer(
        texts.WEB_LOGIN.format(code=code),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.WEB_LOGIN_BUTTON, url=url)]]
        ),
    )


def build_router() -> Router:
    """Новий Router на кожен Dispatcher (aiogram не дозволяє ділити один між кількома)."""
    router = Router(name="common")
    router.message.register(
        start_invite, CommandStart(deep_link=True, magic=F.args.startswith(INVITE_PREFIX))
    )
    # t.me/<bot>?start=web — кнопка «Отримати код» на сторінці входу PWA
    router.message.register(web_login, CommandStart(deep_link=True, magic=F.args == WEB_START_ARG))
    router.message.register(start, CommandStart())
    router.message.register(web_login, Command("web"))
    router.message.register(invite, Command("invite"))
    router.message.register(help_, Command("help"))
    return router
