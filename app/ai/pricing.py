"""Оцінка вартості викликів AI у доларах — для обліку в import_jobs.result.meta.

Ціни (USD за 1 млн токенів) — з прайсу OpenAI на момент написання; звіряти з
https://openai.com/api/pricing і за потреби перевизначати через AI_PRICES у .env (JSON).
Модель зіставляється за префіксом: «gpt-4.1-mini-2025-04-14» → «gpt-4.1-mini».
"""

from typing import Any

# model → {"in": вхідні, "cached": кешовані вхідні, "out": вихідні}
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "gpt-4.1-mini": {"in": 0.40, "cached": 0.10, "out": 1.60},
    "gpt-4.1-nano": {"in": 0.10, "cached": 0.025, "out": 0.40},
    "gpt-4.1": {"in": 2.00, "cached": 0.50, "out": 8.00},
    # транскрипція: «in» — аудіотокени, «out» — текст
    "gpt-4o-mini-transcribe": {"in": 1.25, "cached": 1.25, "out": 5.00},
}


def _price(model: str, prices: dict[str, dict[str, float]]) -> dict[str, float] | None:
    matches = [k for k in prices if model == k or model.startswith(k + "-")]
    return prices[max(matches, key=len)] if matches else None


def cost_usd(
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cached_tokens: int | None = 0,
    prices: dict[str, dict[str, float]] | None = None,
) -> float | None:
    """None — якщо модель невідома або немає токенів (вартість не вгадуємо)."""
    p = _price(model, prices or DEFAULT_PRICES)
    if p is None or prompt_tokens is None or completion_tokens is None:
        return None
    cached = cached_tokens or 0
    usd = (
        (prompt_tokens - cached) * p["in"] + cached * p["cached"] + completion_tokens * p["out"]
    ) / 1_000_000
    return round(usd, 6)


def total_cost(meta: Any) -> float:
    """Сума всіх cost_usd у вкладеній meta задачі (llm, stt, vision, link…)."""
    if isinstance(meta, dict):
        own = meta.get("cost_usd") if isinstance(meta.get("cost_usd"), int | float) else 0
        return round(own + sum(total_cost(v) for k, v in meta.items() if k != "cost_usd"), 6)
    if isinstance(meta, list):
        return round(sum(total_cost(v) for v in meta), 6)
    return 0.0
