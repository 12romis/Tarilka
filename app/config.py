from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["dev", "prod", "test"] = "dev"
    bot_token: str = ""
    webhook_secret: str = ""
    public_base_url: str = ""
    # Перші адміни — числові Telegram ID через кому. Якщо відомий лише username, член сім'ї
    # додається командою `python -m app.cli add-member --username … --admin`.
    admin_tg_ids: Annotated[list[int], NoDecode] = []
    database_url: str = "postgresql+asyncpg://tarilka:tarilka@localhost:5432/tarilka"
    media_dir: str = "/data/media"
    webapp_dir: str = "webapp/dist"  # зібрана Mini App (Vite)
    timezone: str = "Europe/Kyiv"
    invite_ttl_hours: int = 24
    # Лише для ENV=dev: вхід в API без initData від імені цього члена сім'ї.
    dev_auth_member_id: int | None = None
    init_data_max_age_s: int = 24 * 3600

    # AI (лише на етапі вводу)
    llm_provider: Literal["openai"] = "openai"
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1-mini"
    stt_model: str = "gpt-4o-mini-transcribe"

    # Імпорт за посиланням
    ytdlp_cookies_file: str = "/secrets/cookies.txt"  # Netscape-формат; без файлу теж працює
    fetch_timeout_s: float = 15
    max_image_mb: int = 10
    page_text_limit: int = 12000

    # Worker
    worker_concurrency: int = 2
    worker_poll_s: float = 2.0
    job_timeout_s: int = 300
    job_retry_delays_s: Annotated[list[int], NoDecode] = [30, 120]
    draft_autosave_hours: int = 48

    # «Що приготувати?» — ваги алгоритму (див. services/suggest.py)
    suggest_recent_days: int = 3
    suggest_fresh_days: float = 14
    suggest_fresh_min: float = 0.3
    suggest_fresh_max: float = 3.0
    suggest_never_cooked: float = 2.0
    suggest_novelty_k: float = 0.7

    @field_validator("admin_tg_ids", "job_retry_delays_s", mode="before")
    @classmethod
    def _split_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return [int(x) for x in v.replace(" ", "").split(",") if x]
        if isinstance(v, int):
            return [v]
        return v

    @property
    def webhook_url(self) -> str:
        return self.public_base_url.rstrip("/") + "/tg/webhook"


@lru_cache
def get_settings() -> Settings:
    return Settings()
