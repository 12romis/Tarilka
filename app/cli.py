"""Адмін-утиліти: `python -m app.cli add-member --name Ігор --username romis12 --admin`."""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import Dish, ImportJob, Member
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


def _calls(meta: object) -> list[dict]:
    """Усі виклики AI у вкладеній meta (llm, stt, vision, link.llm…)."""
    if isinstance(meta, dict):
        own = [meta] if "model" in meta and "prompt_tokens" in meta else []
        return own + [c for v in meta.values() for c in _calls(v)]
    return []


async def _usage(args: argparse.Namespace) -> None:
    since = datetime.now(UTC) - timedelta(days=args.days)
    rows: dict[str, dict[str, float]] = {}
    async with get_sessionmaker()() as session:
        for job in await session.scalars(select(ImportJob).where(ImportJob.created_at >= since)):
            r = rows.setdefault(
                job.kind.value,
                dict.fromkeys(
                    ("jobs", "calls", "prompt", "cached", "completion", "cost", "unknown_cost"), 0
                ),
            )
            r["jobs"] += 1
            for c in _calls((job.result or {}).get("meta")):
                r["calls"] += 1
                r["prompt"] += c.get("prompt_tokens") or 0
                r["cached"] += c.get("cached_tokens") or 0
                r["completion"] += c.get("completion_tokens") or 0
                if c.get("cost_usd") is None:
                    r["unknown_cost"] += 1  # старі задачі без cost_usd або невідома модель
                else:
                    r["cost"] += c["cost_usd"]
    print(f"AI за {args.days} дн. (оцінка за прайсом з app/ai/pricing.py)")
    print("тип\tзадач\tвикликів\tвхід\tз кешу\tвихід\t$\tбез ціни")
    total = 0.0
    for kind, r in sorted(rows.items()):
        total += r["cost"]
        print(
            f"{kind}\t{r['jobs']:.0f}\t{r['calls']:.0f}\t{r['prompt']:.0f}\t{r['cached']:.0f}\t"
            f"{r['completion']:.0f}\t{r['cost']:.4f}\t{r['unknown_cost']:.0f}"
        )
    print(f"разом: ${total:.4f}")


async def _servings(args: argparse.Namespace) -> None:
    from app.ai.client import build_ai_client
    from app.config import get_settings
    from app.services.servings import fill_servings

    async with get_sessionmaker()() as session:
        result, cost = await fill_servings(
            session, build_ai_client(get_settings()), dry_run=args.dry_run
        )
        titles = dict(
            (await session.execute(select(Dish.id, Dish.title).where(Dish.id.in_(result)))).all()
        )
    for dish_id, n in result.items():
        print(f"{dish_id}\t{n}\t{titles.get(dish_id, '')}")
    verb = "оцінено б" if args.dry_run else "заповнено"
    print(f"{verb} порції: {len(result)} страв, ${cost:.4f}")


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
    usage = sub.add_parser("ai-usage", help="витрати на OpenAI за останні дні")
    usage.add_argument("--days", type=int, default=30)
    usage.set_defaults(func=_usage)
    serv = sub.add_parser("fill-servings", help="AI оцінює порції, де їх не вказано")
    serv.add_argument("--dry-run", action="store_true", help="лише показати, не зберігати")
    serv.set_defaults(func=_servings)
    args = p.parse_args()

    async def run() -> None:
        try:
            await args.func(args)
        finally:
            await dispose_engine()

    asyncio.run(run())


if __name__ == "__main__":
    main()
