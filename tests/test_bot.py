"""Сценарії бота наскрізно через Dispatcher з фейковим Bot API."""

from app import texts
from app.bot.router import build_dispatcher
from app.services.members import add_member
from tests.conftest import feed, make_message_update


async def test_start_for_member(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Ігор", tg_user_id=10, is_admin=True)
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, "/start"))
    assert fake_bot.session.sent_texts() == [texts.START.format(name="Ігор")]


async def test_stranger_warned_once_then_ignored(sessionmaker, fake_bot) -> None:
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(99, "/start"))
    await feed(dp, fake_bot, make_message_update(99, "привіт"))
    assert fake_bot.session.sent_texts() == [texts.PRIVATE_BOT]


async def test_stranger_ignored_in_group(sessionmaker, fake_bot) -> None:
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(99, "/start", chat_type="group"))
    assert fake_bot.session.sent_texts() == []


async def test_username_member_greeted_by_name(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Зірка", username="vesta282")
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(20, "/start", username="Vesta282"))
    assert fake_bot.session.sent_texts() == [texts.START.format(name="Зірка")]


async def test_invite_end_to_end(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Ігор", tg_user_id=10, is_admin=True)
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, "/invite Бабуся"))
    reply = fake_bot.session.sent_texts()[-1]
    assert "«Бабуся»" in reply
    start_arg = reply.rsplit("?start=", 1)[1]
    assert start_arg.startswith("inv_")

    await feed(dp, fake_bot, make_message_update(30, f"/start {start_arg}", first_name="Галя"))
    greeting = fake_bot.session.sent_texts()[-1]
    assert greeting.startswith(texts.START_JOINED.format(name="Бабуся"))

    # тепер новий член проходить білий список
    await feed(dp, fake_bot, make_message_update(30, "/start"))
    assert fake_bot.session.sent_texts()[-1] == texts.START.format(name="Бабуся")


async def test_invalid_invite_link(sessionmaker, fake_bot) -> None:
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(31, "/start inv_bogus"))
    assert fake_bot.session.sent_texts() == [texts.INVITE_INVALID]


async def test_invite_admin_only(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Зірка", tg_user_id=20)
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(20, "/invite"))
    assert fake_bot.session.sent_texts() == [texts.INVITE_ADMIN_ONLY]


async def test_help(sessionmaker, session, fake_bot) -> None:
    await add_member(session, "Ігор", tg_user_id=10)
    dp = build_dispatcher(sessionmaker, [])
    await feed(dp, fake_bot, make_message_update(10, "/help"))
    msg = fake_bot.session.sent()[-1]
    assert msg.parse_mode == "HTML" and msg.text.startswith("<b>🍽 Тарілка")
    assert "@tarilka_test_bot" in msg.text


def test_help_is_valid_html_and_descriptions_fit() -> None:
    from html.parser import HTMLParser

    class Check(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.stack: list[str] = []

        def handle_starttag(self, tag, attrs):
            assert tag in {"b", "i"}, tag
            self.stack.append(tag)

        def handle_endtag(self, tag):
            assert self.stack.pop() == tag

    c = Check()
    c.feed(texts.HELP.format(bot_username="x"))
    assert c.stack == []
    assert len(texts.HELP) < 4096
    assert len(texts.BOT_DESCRIPTION) <= 512
    assert len(texts.BOT_SHORT_DESCRIPTION) <= 120
    assert "help" in texts.COMMANDS
