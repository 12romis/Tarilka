from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup


async def safe_edit(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    markup: InlineKeyboardMarkup | None,
    parse_mode: str | None = None,
) -> None:
    """Оновлює картку: текст або (для повідомлення з фото) підпис.

    Не падає, коли текст і кнопки не змінилися.
    """
    try:
        await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=markup,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        if "no text in the message" not in str(e):
            raise
        try:
            await bot.edit_message_caption(
                caption=caption(text),
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=markup,
                parse_mode=parse_mode,
            )
        except TelegramBadRequest as e2:
            if "message is not modified" not in str(e2):
                raise


CAPTION_LIMIT = 1024


def caption(text: str) -> str:
    return text if len(text) <= CAPTION_LIMIT else text[: CAPTION_LIMIT - 1] + "…"
