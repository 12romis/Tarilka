"""Тексти й клавіатури карток: чернетка, зведена картка, меню-перемикачі, пропозиції."""

from datetime import datetime

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import texts
from app.bot.keyboards import DraftCb
from app.db.models import ImportJob
from app.services import drafts as dr
from app.services.dishes import DishInfo
from app.services.draft_fields import Draft, tag_slugs
from app.services.meta import KIND_ORDER, FamilyMeta

Card = tuple[str, InlineKeyboardMarkup | None]


def _title_emoji(meta: FamilyMeta, d: Draft) -> str:
    for slug in d.get("course") or []:
        if (t := meta.tag("course", slug)) and t.emoji:
            return t.emoji
    return "🍽"


def _labels(meta: FamilyMeta, kind: str, slugs: list[str], with_emoji: bool = False) -> list[str]:
    out = []
    for s in slugs:
        if t := meta.tag(kind, s):
            out.append(f"{t.emoji} {t.label}" if with_emoji and t.emoji else t.label)
    return out


def _cook_names(meta: FamilyMeta, d: Draft) -> list[str]:
    return [meta.member_by_id[c].name for c in d.get("cooks") or [] if c in meta.member_by_id]


def draft_text(meta: FamilyMeta, d: Draft) -> str:
    """Опис однієї чернетки (без кнопок)."""
    line: list[str] = []
    cooks = _cook_names(meta, d)
    if cooks:
        line.append("👤 " + ", ".join(cooks))
    line += _labels(meta, "meal", d.get("meal") or [], with_emoji=True)
    if d.get("main_ingredient"):
        line += _labels(meta, "ingredient", [d["main_ingredient"]], with_emoji=True)
    if d.get("cook_time_min"):
        line.append(f"⏱ {d['cook_time_min']} хв")
    other = (
        _labels(meta, "course", d.get("course") or [])
        + _labels(meta, "ingredient", d.get("ingredient_tags") or [])
        + _labels(meta, "trait", d.get("traits") or [])
        + _labels(meta, "cuisine", [d["cuisine"]] if d.get("cuisine") else [])
    )
    parts = [f"{_title_emoji(meta, d)} {d['title']}"]
    if line:
        parts.append("  ·  ".join(line))
    parts.append(texts.STATUS_LINE.format(status=texts.STATUS[d.get("status") or "can_cook"]))
    if other:
        parts.append("🏷 " + ", ".join(other))
    if d.get("ingredients"):
        parts.append(texts.HAS_INGREDIENTS.format(n=len(d["ingredients"])))
    if d.get("source_url"):
        parts.append(texts.SOURCE_LINE.format(where=source_label(d)))
    if d.get("notes"):
        parts.append(d["notes"])
    return "\n".join(parts)


def source_label(d: Draft) -> str:
    """Коротко, звідки рецепт: «TikTok @автор» або домен сайту."""
    from urllib.parse import urlsplit

    platform = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube"}.get(
        d.get("source_type") or ""
    )
    if platform:
        return f"{platform} {d['source_author']}" if d.get("source_author") else platform
    return (urlsplit(d["source_url"]).hostname or d["source_url"]).removeprefix("www.")


def _header(job: ImportJob) -> str:
    transcript = (job.result or {}).get("transcript")
    return texts.HEARD.format(text=transcript[:500]) + "\n\n" if transcript else ""


def render_job(meta: FamilyMeta, job: ImportJob) -> Card:
    """Поточний вигляд задачі: зведена картка, картка чернетки або підсумок."""
    ds = dr.drafts(job)
    pending = dr.pending_indexes(job)
    if not pending:
        return final_text(job), None
    view = (job.result or {}).get("view", "summary")
    if len(ds) == 1:
        return render_draft(meta, job, 0)
    if view == "summary" or not isinstance(view, int) or view not in pending:
        return render_summary(meta, job)
    return render_draft(meta, job, view)


def final_text(job: ImportJob) -> str:
    saved = [
        d["title"] + (texts.MERGED_MARK if d.get("merged") else "")
        for d in dr.drafts(job)
        if d["state"] == dr.SAVED
    ]
    if not saved:
        return texts.DRAFT_DISCARDED
    return texts.SAVED_DISHES.format(n=len(saved), titles=", ".join(saved))


def render_summary(meta: FamilyMeta, job: ImportJob) -> Card:
    ds = dr.drafts(job)
    lines = []
    for n, d in enumerate(ds, 1):
        mark = {"saved": "✅ ", "discarded": "🗑 "}.get(d["state"], "")
        if d["state"] == dr.PENDING and d.get("existing_id"):
            mark = "♻️ "
        cooks = _cook_names(meta, d)
        extra = "  ·  ".join(
            (["👤 " + ", ".join(cooks)] if cooks else [])
            + _labels(meta, "meal", d.get("meal") or [], with_emoji=True)
        )
        lines.append(
            f"{n}. {mark}{_title_emoji(meta, d)} {d['title']}" + (f" — {extra}" if extra else "")
        )
    text = _header(job) + texts.FOUND_N.format(n=len(ds)) + "\n\n" + "\n".join(lines)
    if any(d["state"] == dr.PENDING and d.get("existing_id") for d in ds):
        text += "\n\n" + texts.ALREADY_EXISTS_SUMMARY
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_SAVE_ALL, callback_data=DraftCb(job=job.id, i=-1, a="all"))
    kb.button(text=texts.BTN_ONE_BY_ONE, callback_data=DraftCb(job=job.id, i=-1, a="one"))
    kb.button(text=texts.BTN_DISCARD_ALL, callback_data=DraftCb(job=job.id, i=-1, a="del"))
    kb.adjust(1, 2)
    return text, kb.as_markup()


def render_draft(meta: FamilyMeta, job: ImportJob, i: int) -> Card:
    ds = dr.drafts(job)
    d = ds[i]
    head = _header(job)
    if len(ds) > 1:
        head += texts.DISH_N_OF.format(n=i + 1, total=len(ds)) + "\n"
    text = head + draft_text(meta, d)
    if d.get("existing_id"):
        text += "\n\n" + texts.ALREADY_EXISTS
    cb = lambda a, arg="": DraftCb(job=job.id, i=i, a=a, arg=arg)  # noqa: E731
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_SAVE, callback_data=cb("save"))
    kb.button(text=texts.BTN_COOKS, callback_data=cb("cooks"))
    kb.button(text=texts.BTN_TAGS, callback_data=cb("tags"))
    kb.button(text=texts.BTN_RENAME, callback_data=cb("ren"))
    to = "want_to_try" if d.get("status") == "can_cook" else "can_cook"
    kb.button(text=texts.BTN_STATUS.format(status=texts.STATUS[to]), callback_data=cb("st"))
    kb.button(text=texts.BTN_DISCARD, callback_data=cb("del"))
    sizes = [1, 2, 2, 1]
    if d.get("source_url"):
        kb.button(text=texts.BTN_OPEN_SOURCE, url=d["source_url"])
        sizes.append(1)
    if len(ds) > 1:
        kb.button(text=texts.BTN_TO_LIST, callback_data=cb("sum"))
        kb.button(text=texts.BTN_NEXT, callback_data=cb("next"))
        sizes.append(2)
    kb.adjust(*sizes)
    return text, kb.as_markup()


def render_cooks_menu(meta: FamilyMeta, job: ImportJob, i: int) -> Card:
    d = dr.drafts(job)[i]
    kb = InlineKeyboardBuilder()
    for m in meta.members:
        on = m.id in (d.get("cooks") or [])
        kb.button(
            text=("☑️ " if on else "▫️ ") + m.name,
            callback_data=DraftCb(job=job.id, i=i, a="tc", arg=str(m.id)),
        )
    kb.button(text=texts.BTN_BACK, callback_data=DraftCb(job=job.id, i=i, a="card"))
    kb.adjust(2)
    return texts.MENU_COOKS.format(title=d["title"]), kb.as_markup()


def render_tag_kinds(meta: FamilyMeta, job: ImportJob, i: int) -> Card:
    d = dr.drafts(job)[i]
    kb = InlineKeyboardBuilder()
    for kind in KIND_ORDER:
        n = len(tag_slugs(d, kind.value))
        kb.button(
            text=texts.TAG_KINDS[kind.value] + (f" ({n})" if n else ""),
            callback_data=DraftCb(job=job.id, i=i, a="tk", arg=kind.value),
        )
    kb.button(text=texts.BTN_BACK, callback_data=DraftCb(job=job.id, i=i, a="card"))
    kb.adjust(1)
    return texts.MENU_TAGS.format(title=d["title"]), kb.as_markup()


def render_tag_toggles(meta: FamilyMeta, job: ImportJob, i: int, kind: str) -> Card:
    d = dr.drafts(job)[i]
    selected = tag_slugs(d, kind)
    kb = InlineKeyboardBuilder()
    for t in meta.kind_tags(kind):
        if t.slug in selected:
            mark = "⭐ " if kind == "ingredient" and t.slug == selected[0] else "☑️ "
        else:
            mark = ""
        kb.button(
            text=f"{mark}{t.emoji or ''} {t.label}".strip(),
            callback_data=DraftCb(job=job.id, i=i, a="tt", arg=f"{kind}.{t.slug}"),
        )
    kb.button(text=texts.BTN_BACK, callback_data=DraftCb(job=job.id, i=i, a="tags"))
    kb.adjust(2)
    hint = texts.MAIN_INGREDIENT_HINT if kind == "ingredient" else ""
    return texts.MENU_TAG_KIND.format(
        kind=texts.TAG_KINDS[kind], title=d["title"]
    ) + hint, kb.as_markup()


def _ago(now: datetime, dt: datetime | None) -> str:
    if dt is None:
        return texts.NEVER_COOKED
    days = (now - dt).days
    return texts.COOKED_TODAY if days == 0 else texts.COOKED_DAYS_AGO.format(n=days)


def dish_line(info: DishInfo, now: datetime) -> str:
    d = info.dish
    emoji = next((t.emoji for t in info.tags if t.kind.value == "course" and t.emoji), "🍽")
    parts = []
    if info.cooks:
        parts.append("👤 " + ", ".join(m.name for m in info.cooks))
    if d.cook_time_min:
        parts.append(f"⏱ {d.cook_time_min} хв")
    if d.status.value == "want_to_try":
        parts.append(texts.STATUS["want_to_try"])
    parts.append(_ago(now, d.last_cooked_at))
    return f"{emoji} {d.title}\n     " + "  ·  ".join(parts)
