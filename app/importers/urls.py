"""Посилання: пошук у тексті, розгортання коротких, очищення, тип джерела."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.db.models import SourceType
from app.services.media import BROWSER_UA

URL_RE = re.compile(r"https?://[^\s<>\"'«»]+", re.IGNORECASE)

SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com", "youtu.be", "fb.watch", "pin.it"}
# Для відеоплатформ query не потрібен зовсім (там лише трекінг).
DROP_ALL_QUERY = {"tiktok.com", "instagram.com"}
TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|igsh|igshid|si|_r|_t|ref|share_.*|is_from_webapp)$")

PLATFORMS: list[tuple[str, SourceType]] = [
    ("tiktok.com", SourceType.tiktok),
    ("instagram.com", SourceType.instagram),
    ("youtube.com", SourceType.youtube),
    ("youtu.be", SourceType.youtube),
]
VIDEO_TYPES = {SourceType.tiktok, SourceType.instagram, SourceType.youtube}


def find_urls(text: str) -> list[str]:
    return [u.rstrip(".,;:!?)»") for u in URL_RE.findall(text or "")]


def strip_urls(text: str) -> str:
    return " ".join(URL_RE.sub(" ", text or "").split())


def _host(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.").removeprefix("m.")


def source_type_of(url: str) -> SourceType:
    host = _host(url)
    for domain, st in PLATFORMS:
        if host == domain or host.endswith("." + domain):
            return st
    return SourceType.web


def clean_url(url: str) -> str:
    """Прибирає трекінг-параметри й фрагмент; для TikTok/Instagram — увесь query."""
    parts = urlsplit(url.strip())
    host = _host(url)
    if any(host == d or host.endswith("." + d) for d in DROP_ALL_QUERY):
        query = ""
    else:
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k)])
    path = parts.path
    if host.endswith("instagram.com") and not path.endswith("/"):
        path += "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


async def normalize_url(client: httpx.AsyncClient, url: str) -> str:
    """Розгортає короткі посилання (vt.tiktok.com, youtu.be…) і чистить результат."""
    if _host(url) in SHORT_HOSTS:
        try:
            r = await client.head(url, follow_redirects=True, headers={"User-Agent": BROWSER_UA})
            url = str(r.url)
        except httpx.HTTPError:
            pass  # лишаємо коротке — воно теж відкривається
    return clean_url(url)


def author_from_url(url: str) -> str | None:
    """@нік із шляху TikTok (tiktok.com/@user/video/…)."""
    m = re.search(r"/@([^/?#]+)", url)
    return f"@{m.group(1)}" if m else None
