"""Session handling and the `current_user` dependency that protects routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional

from fastapi import HTTPException, Request, Response

from backend.app.auth.security import hash_token, new_session_token
from backend.app.core.config import settings
from backend.app.db.mongo import db

COOKIE_NAME = "krypto_session"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def start_session(response: Response, user_id: Any) -> None:
    """Create a session row and set the HttpOnly cookie that carries its token."""
    token = new_session_token()
    expires = _now() + timedelta(days=settings.SESSION_DAYS)
    await db.sessions.insert_one({"token_hash": hash_token(token), "user_id": user_id, "expires_at": expires})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.SESSION_DAYS * 86400,
        httponly=True,       # not readable from JavaScript, so an XSS bug can't steal it
        samesite="lax",      # not sent on cross-site POSTs (CSRF protection)
        secure=settings.COOKIE_SECURE,
        path="/",
    )


async def end_session(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await db.sessions.delete_one({"token_hash": hash_token(token)})
    response.delete_cookie(COOKIE_NAME, path="/")


async def user_from_cookies(cookies: Mapping[str, str]) -> Optional[Dict[str, Any]]:
    """The signed-in user for a set of cookies, or None (used by WebSocket connections)."""
    token = cookies.get(COOKIE_NAME)
    if not token:
        return None
    session = await db.sessions.find_one({"token_hash": hash_token(token)})
    if session is None:
        return None
    expires = session["expires_at"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= _now():
        return None
    return await db.users.find_one({"_id": session["user_id"]})


async def current_user(request: Request) -> Dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not signed in")

    session = await db.sessions.find_one({"token_hash": hash_token(token)})
    expires = session["expires_at"] if session else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if session is None or expires <= _now():
        raise HTTPException(status_code=401, detail="Session expired, please sign in again")

    user = await db.users.find_one({"_id": session["user_id"]})
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user
