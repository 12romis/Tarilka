"""Оптимізації звернень до OpenAI: облік вартості й кешу, без зайвих кроків, менші фото."""

import io
from types import SimpleNamespace

from PIL import Image

from app.ai.client import OpenAIClient
from app.ai.pricing import cost_usd, total_cost
from app.ai.schemas import TextParse
from app.cli import _calls
from app.config import get_settings
from app.db.models import SourceType
from app.importers.base import RawExtract
from app.importers.link import STEPS_PROVIDED, link_user_prompt
from app.services.media import shrink_for_vision


def test_cost_prefix_match_and_cache_discount() -> None:
    full = cost_usd("gpt-4.1-mini-2025-04-14", 2000, 400, 0)
    cached = cost_usd("gpt-4.1-mini-2025-04-14", 2000, 400, 1536)
    assert full == round((2000 * 0.40 + 400 * 1.60) / 1e6, 6)
    assert cached is not None and cached < full
    # «gpt-4.1» не має перехоплювати «gpt-4.1-mini» — береться найдовший префікс
    assert cost_usd("gpt-4.1-mini", 1_000_000, 0) == 0.40
    assert cost_usd("unknown-model", 10, 10) is None
    assert cost_usd("gpt-4.1-mini", None, 10) is None


def test_total_cost_sums_nested_calls() -> None:
    meta = {
        "stt": {"cost_usd": 0.001},
        "llm": {"cost_usd": 0.002},
        "link": {"llm": {"cost_usd": 0.003}},
        "video": {"cookies": False},
    }
    assert total_cost(meta) == 0.006
    assert len(_calls({"llm": {"model": "m", "prompt_tokens": 1}, "x": {"y": 1}})) == 1


async def test_client_logs_cached_tokens_and_cost(monkeypatch) -> None:
    client = OpenAIClient(get_settings().model_copy(update={"llm_api_key": "sk-test"}))
    parsed = TextParse(dishes=[])
    usage = SimpleNamespace(
        prompt_tokens=2000,
        completion_tokens=100,
        prompt_tokens_details=SimpleNamespace(cached_tokens=1792),
    )
    resp = SimpleNamespace(
        model="gpt-4.1-mini-2025-04-14",
        usage=usage,
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
    )

    async def fake_parse(**_):
        return resp

    monkeypatch.setattr(client._client.chat.completions, "parse", fake_parse)
    _, meta = await client.structured("s", "u", TextParse)
    assert meta["cached_tokens"] == 1792
    assert meta["cost_usd"] == cost_usd("gpt-4.1-mini", 2000, 100, 1792)


def test_structured_steps_not_sent_to_llm() -> None:
    raw = RawExtract(
        url="https://s.test/r",
        source_type=SourceType.web,
        title="Борщ",
        structured={
            "title": "Борщ",
            "ingredients": ["буряк"],
            "steps": ["Крок один дуже довгий"] * 5,
        },
    )
    prompt = link_user_prompt(raw, "")
    assert "Крок один" not in prompt and STEPS_PROVIDED in prompt and "буряк" in prompt
    raw.structured["steps"] = []
    assert STEPS_PROVIDED not in link_user_prompt(raw, "")


def _jpeg(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(buf, "JPEG")
    return buf.getvalue()


def test_shrink_for_vision() -> None:
    big = shrink_for_vision(_jpeg(2560, 1440), 1024)
    assert Image.open(io.BytesIO(big)).size == (1024, 576)
    small = _jpeg(800, 600)
    assert shrink_for_vision(small, 1024) is small  # малий JPEG не перекодовуємо
    png = io.BytesIO()
    Image.new("RGBA", (300, 300)).save(png, "PNG")
    assert shrink_for_vision(png.getvalue(), 1024)[:3] == b"\xff\xd8\xff"
