"""Текстовий ввід (і розшифровка голосового) → список чернеток через один виклик LLM."""

from typing import Any

from app.ai.client import AIClient
from app.ai.prompts import text_system_prompt
from app.ai.schemas import TextParse, clean_draft
from app.db.models import ImportJob
from app.importers.base import ImportContext, ImportOutput
from app.services.meta import FamilyMeta


async def parse_text(
    ai: AIClient, meta: FamilyMeta, text: str, author_id: int | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    system = text_system_prompt(meta.prompt_members(), meta.prompt_tags())
    parsed, ai_meta = await ai.structured(system, text, TextParse)
    drafts = [
        clean_draft(
            d, slugs=meta.slugs(), member_ids=set(meta.member_by_id), default_cook=author_id
        )
        for d in parsed.dishes
        if d.title.strip()
    ]
    return drafts, ai_meta


class TextImporter:
    async def run(self, job: ImportJob, ctx: ImportContext) -> ImportOutput:
        text = job.payload["text"]
        drafts, ai_meta = await parse_text(ctx.ai, ctx.meta, text, job.created_by)
        return ImportOutput(drafts=drafts, source_text=text, meta={"llm": ai_meta})
