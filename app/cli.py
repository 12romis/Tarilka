"""Адмін-утиліти: `python -m app.cli add-member --name Ігор --username romis12 --admin`."""

import argparse
import asyncio

from sqlalchemy import select

from app.db.models import Member
from app.db.session import dispose_engine, get_sessionmaker
from app.services.members import add_member


async def _add(args: argparse.Namespace) -> None:
    async with get_sessionmaker()() as session:
        m = await add_member(
            session,
            args.name,
            username=args.username,
            tg_user_id=args.tg_id,
            is_admin=args.admin,
            aliases=args.alias,
        )
        print(f"Додано: id={m.id} {m.name} @{m.tg_username or '-'} admin={m.is_admin}")


async def _list(_: argparse.Namespace) -> None:
    async with get_sessionmaker()() as session:
        for m in await session.scalars(select(Member).order_by(Member.id)):
            print(
                f"{m.id}\t{m.name}\t@{m.tg_username or '-'}\ttg_id={m.tg_user_id or '-'}"
                f"\tadmin={m.is_admin}\taliases={','.join(m.aliases)}"
            )


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(required=True)
    add = sub.add_parser("add-member", help="додати члена сім'ї")
    add.add_argument("--name", required=True)
    add.add_argument("--username", help="Telegram username, прив'яжеться при першому повідомленні")
    add.add_argument("--tg-id", type=int)
    add.add_argument("--admin", action="store_true")
    add.add_argument("--alias", action="append", default=[], help="синонім імені для AI")
    add.set_defaults(func=_add)
    sub.add_parser("list-members").set_defaults(func=_list)
    args = p.parse_args()

    async def run() -> None:
        try:
            await args.func(args)
        finally:
            await dispose_engine()

    asyncio.run(run())


if __name__ == "__main__":
    main()
