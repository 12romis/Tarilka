"""Вхід у браузер/PWA: обмін одноразового посилання чи коду на cookie-сесію."""

import time
from collections import defaultdict, deque
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.api.auth import SESSION_COOKIE, CurrentMember
from app.api.meta import SessionDep, SettingsDep
from app.services import web_auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


class FailLimiter:
    """Не більше `limit` невдалих спроб з однієї IP за `window_s` — від перебору кодів."""

    def __init__(self, limit: int = 10, window_s: float = 15 * 60) -> None:
        self.limit, self.window_s = limit, window_s
        self.fails: dict[str, deque[float]] = defaultdict(deque)

    def blocked(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self.fails[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        return len(q) >= self.limit

    def fail(self, key: str, now: float | None = None) -> None:
        self.fails[key].append(time.monotonic() if now is None else now)


limiter = FailLimiter()


class LinkOut(BaseModel):
    url: str
    code: str
    expires_at: datetime


class LoginIn(BaseModel):
    token: str | None = None
    code: str | None = None


class SessionOut(BaseModel):
    id: int
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime
    current: bool


@router.post("/link", response_model=LinkOut)
async def create_link(member: CurrentMember, session: SessionDep, settings: SettingsDep) -> LinkOut:
    """Посилання для відкриття Тарілки в браузері (кнопка в Mini App)."""
    token, code, expires = await web_auth.create_login_link(session, member)
    return LinkOut(
        url=web_auth.login_url(settings.public_base_url, token), code=code, expires_at=expires
    )


@router.post("/login", status_code=204)
async def login(
    body: LoginIn, request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> None:
    ip = request.client.host if request.client else "?"
    if limiter.blocked(ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Забагато спроб, зачекай 15 хв")
    member = await web_auth.redeem(session, token=body.token, code=(body.code or "").strip())
    if member is None:
        limiter.fail(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Посилання або код недійсні")
    token = await web_auth.create_session(session, member, request.headers.get("user-agent"))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(web_auth.SESSION_TTL.total_seconds()),
        httponly=True,
        secure=settings.public_base_url.startswith("https://"),
        samesite="lax",
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, member: CurrentMember, session: SessionDep
) -> None:
    if sid := getattr(request.state, "web_session_id", None):
        await web_auth.revoke(session, member, sid)
    response.delete_cookie(SESSION_COOKIE)


@router.get("/sessions", response_model=list[SessionOut])
async def sessions(
    request: Request, member: CurrentMember, session: SessionDep
) -> list[SessionOut]:
    current = getattr(request.state, "web_session_id", None)
    return [
        SessionOut(
            id=s.id,
            user_agent=s.user_agent,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            current=s.id == current,
        )
        for s in await web_auth.list_sessions(session, member)
    ]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: int, member: CurrentMember, session: SessionDep) -> None:
    if not await web_auth.revoke(session, member, session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
