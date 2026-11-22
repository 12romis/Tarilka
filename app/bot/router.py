from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import texts
from app.bot.handlers import common
from app.bot.middlewares import AccessMiddleware
from app.config import Settings

ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member"]


def build_bot(settings: Settings) -> Bot:
    return Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=None))


def build_dispatcher(
    sessionmaker: async_sessionmaker[AsyncSession], admin_ids: list[int]
) -> Dispatcher:
    dp = Dispatcher()
    access = AccessMiddleware(sessionmaker, admin_ids)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_router(common.build_router())
    return dp


async def setup_telegram(bot: Bot, settings: Settings) -> None:
    """Вебхук і меню команд; викликається при старті app у проді."""
    await bot.set_webhook(
        url=settings.webhook_url,
        secret_token=settings.webhook_secret,
        allowed_updates=ALLOWED_UPDATES,
        drop_pending_updates=False,
    )
    commands = [BotCommand(command=c, description=d) for c, d in texts.COMMANDS.items()]
    await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
