from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    MenuButtonWebApp,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import texts
from app.ai.client import AIClient
from app.bot.handlers import add, admin, common, drafts, shopping, what
from app.bot.middlewares import AccessMiddleware
from app.config import Settings

ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member"]


def build_bot(settings: Settings) -> Bot:
    return Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=None))


def build_dispatcher(
    sessionmaker: async_sessionmaker[AsyncSession],
    admin_ids: list[int],
    ai: AIClient | None = None,
) -> Dispatcher:
    dp = Dispatcher(ai=ai)  # ai — для коротких синхронних викликів (список покупок)
    access = AccessMiddleware(sessionmaker, admin_ids)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    # Порядок важливий: команди й кнопки — раніше за «будь-який текст» з add.
    for module in (common, admin, what, shopping, drafts, add):
        dp.include_router(module.build_router())
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
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text=texts.MENU_BUTTON,
            web_app=WebAppInfo(url=settings.public_base_url.rstrip("/") + "/"),
        )
    )
    await bot.set_my_description(texts.BOT_DESCRIPTION)
    await bot.set_my_short_description(texts.BOT_SHORT_DESCRIPTION)
