"""Єдиний вхід до AI: structured output, розпізнавання мовлення, vision.

Провайдер обирається через LLM_PROVIDER (поки лише openai). Кожен виклик повертає meta
(модель, токени, час) — worker зберігає її в import_jobs.result.meta.
"""

import base64
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from app.ai.pricing import DEFAULT_PRICES, cost_usd
from app.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)


class AIError(Exception):
    """Будь-яка помилка AI-провайдера; клієнти загортають у неї власні винятки SDK."""


class AIClient(Protocol):
    async def structured(
        self, system: str, user: str, schema: type[T], images: list[bytes] | None = None
    ) -> tuple[T, dict[str, Any]]: ...

    async def transcribe(self, audio_path: Path) -> tuple[str, dict[str, Any]]: ...


class OpenAIClient:
    def __init__(self, settings: Settings) -> None:
        from openai import AsyncOpenAI, OpenAIError
        from pydantic import ValidationError

        # Помилки API/мережі та невалідна structured-відповідь → AIError.
        self._sdk_errors = (OpenAIError, ValidationError)
        self._client = AsyncOpenAI(api_key=settings.llm_api_key, timeout=90, max_retries=1)
        self.model = settings.llm_model
        self.stt_model = settings.stt_model
        self.prices = {**DEFAULT_PRICES, **settings.ai_prices}

    async def structured(
        self, system: str, user: str, schema: type[T], images: list[bytes] | None = None
    ) -> tuple[T, dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for img in images or []:
            b64 = base64.b64encode(img).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            )
        started = time.monotonic()
        try:
            resp = await self._client.chat.completions.parse(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                response_format=schema,
            )
        except self._sdk_errors as e:
            raise AIError(repr(e)) from e
        msg = resp.choices[0].message
        if msg.parsed is None:
            raise AIError(msg.refusal or "модель не повернула відповідь")
        usage = resp.usage
        prompt = usage.prompt_tokens if usage else None
        completion = usage.completion_tokens if usage else None
        details = usage.prompt_tokens_details if usage else None
        # OpenAI сам кешує однаковий початок промпта від 1024 токенів — дешевше в ~4 рази.
        cached = (details.cached_tokens or 0) if details else 0
        return msg.parsed, {
            "model": resp.model,
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "completion_tokens": completion,
            "cost_usd": cost_usd(resp.model, prompt, completion, cached, self.prices),
            "seconds": round(time.monotonic() - started, 2),
        }

    async def vision(
        self, system: str, user: str, schema: type[T], images: list[bytes]
    ) -> tuple[T, dict[str, Any]]:
        return await self.structured(system, user, schema, images)

    async def transcribe(self, audio_path: Path) -> tuple[str, dict[str, Any]]:
        started = time.monotonic()
        try:
            with audio_path.open("rb") as f:
                resp = await self._client.audio.transcriptions.create(
                    model=self.stt_model, file=f, language="uk"
                )
        except self._sdk_errors as e:
            raise AIError(repr(e)) from e
        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "input_tokens", None)
        tokens_out = getattr(usage, "output_tokens", None)
        return resp.text.strip(), {
            "model": self.stt_model,
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "cost_usd": cost_usd(self.stt_model, tokens_in, tokens_out, 0, self.prices),
            "seconds": round(time.monotonic() - started, 2),
        }


def build_ai_client(settings: Settings) -> AIClient:
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY не задано")
    return OpenAIClient(settings)


@lru_cache
def default_ai() -> AIClient | None:
    """Спільний клієнт для API й бота; None — ключ не задано (функції працюють без AI)."""
    settings = get_settings()
    return build_ai_client(settings) if settings.llm_api_key else None
