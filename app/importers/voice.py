"""Голосове: файл із Telegram → STT → далі як текст."""

from app import texts
from app.bot.handlers.pantry import pantry_request
from app.bot.handlers.shopping import shopping_request
from app.db.models import ImportJob, SourceType
from app.importers.base import HouseholdText, ImportContext, ImportOutput, PermanentImportError
from app.importers.text import parse_text


class VoiceImporter:
    async def run(self, job: ImportJob, ctx: ImportContext) -> ImportOutput:
        path = ctx.tmp_dir / "voice.ogg"
        await ctx.bot.download(job.payload["file_id"], destination=path)
        transcript, stt_meta = await ctx.ai.transcribe(path)
        if not transcript:
            raise PermanentImportError(texts.REASON_VOICE)
        if shopping_request(transcript) is not None or pantry_request(transcript) is not None:
            raise HouseholdText(transcript)
        drafts, ai_meta = await parse_text(ctx.ai, ctx.meta, transcript, job.created_by)
        return ImportOutput(
            drafts=drafts,
            source_text=transcript,
            source_type=SourceType.voice,
            transcript=transcript,
            meta={"stt": stt_meta, "llm": ai_meta},
        )
