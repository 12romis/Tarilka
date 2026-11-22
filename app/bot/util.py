from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup


async def safe_edit(
    bot: Bot, chat_id: int, message_id: int, text: str, markup: InlineKeyboardMarkup | None
) -> None:
    """edit_message_text, що не падає, коли текст і кнопки не змінилися."""
    try:
        await bot.edit_message_text(
            text=text, chat_id=chat_id, message_id=message_id, reply_markup=markup
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
