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
    timezone: str = "Europe/Kyiv"
    invite_ttl_hours: int = 24
    # Лише для ENV=dev: вхід в API без initData від імені цього члена сім'ї.
    dev_auth_member_id: int | None = None
    init_data_max_age_s: int = 24 * 3600

    @field_validator("admin_tg_ids", mode="before")
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
