import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from aiogram.types import Update
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import meta
from app.bot.router import build_bot, build_dispatcher, setup_telegram
from app.config import get_settings
from app.db.session import dispose_engine, get_session, get_sessionmaker

log = logging.getLogger("tarilka")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.bot = build_bot(settings) if settings.bot_token else None
    app.state.dp = build_dispatcher(get_sessionmaker(), settings.admin_tg_ids)
    if app.state.bot and settings.env == "prod":
        await setup_telegram(app.state.bot, settings)
        log.info("Вебхук встановлено: %s", settings.webhook_url)
    yield
    if app.state.bot:
        await app.state.bot.session.close()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Tarilka", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )
    app.include_router(meta.router)

    @app.post("/tg/webhook", include_in_schema=False)
    async def tg_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        expected = get_settings().webhook_secret
        if not expected or not secrets.compare_digest(
            x_telegram_bot_api_secret_token or "", expected
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN)
        bot = request.app.state.bot
        if bot is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE)
        update = Update.model_validate(await request.json(), context={"bot": bot})
        await request.app.state.dp.feed_update(bot, update)
        return {"ok": True}

    @app.get("/healthz", include_in_schema=False)
    async def healthz(session: Annotated[AsyncSession, Depends(get_session)]) -> JSONResponse:
        try:
            row = (
                await session.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE status = 'queued'), "
                        "count(*) FILTER (WHERE status = 'failed' "
                        "AND updated_at > now() - interval '24 hours') FROM import_jobs"
                    )
                )
            ).one()
        except Exception:
            log.exception("healthz: БД недоступна")
            return JSONResponse(
                {"status": "error", "db": "down"}, status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return JSONResponse({"status": "ok", "db": "up", "queued": row[0], "failed_24h": row[1]})

    media = Path(settings.media_dir)
    if media.is_dir():
        app.mount("/media", StaticFiles(directory=media), name="media")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
