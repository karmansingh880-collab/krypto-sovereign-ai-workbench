"""Sign up, sign in, sign out, and the one-click demo account."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from backend.app.auth.deps import current_user, end_session, start_session
from backend.app.auth.security import DUMMY_HASH, hash_password, verify_password
from backend.app.core.config import settings
from backend.app.db.mongo import db

router = APIRouter(prefix="/auth", tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD, MAX_PASSWORD = 8, 128

# Failed-login throttle: 5 misses per (client, email) in 5 minutes, then a lockout.
MAX_FAILURES, WINDOW_SECONDS = 5, 300
_failures: Dict[str, list] = {}


class SignupBody(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginBody(BaseModel):
    email: str
    password: str


def _public(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(user["_id"]),
        "email": user["email"],
        "name": user.get("name") or user["email"].split("@")[0],
        "is_demo": bool(user.get("is_demo")),
    }


def _clean_email(raw: str) -> str:
    email = raw.strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return email


def _throttle_key(request: Request, email: str) -> str:
    client = request.client.host if request.client else "?"
    return f"{client}|{email}"


def _check_throttle(key: str) -> None:
    now = time.monotonic()
    if len(_failures) > 5000:  # forget expired entries so random emails can't grow this forever
        for stale in [k for k, times in _failures.items() if not times or now - times[-1] >= WINDOW_SECONDS]:
            del _failures[stale]
    recent = [t for t in _failures.get(key, []) if now - t < WINDOW_SECONDS]
    _failures[key] = recent
    if len(recent) >= MAX_FAILURES:
        wait = int(WINDOW_SECONDS - (now - recent[0])) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )


@router.get("/config")
async def auth_config():
    """Lets the login page offer the demo account when it is enabled."""
    if not settings.DEMO_ACCOUNT_ENABLED:
        return {"demo_enabled": False}
    return {
        "demo_enabled": True,
        "demo_email": settings.DEMO_EMAIL,
        "demo_password": settings.DEMO_PASSWORD,
    }


@router.post("/signup", status_code=201)
async def signup(body: SignupBody, response: Response):
    email = _clean_email(body.email)
    if not MIN_PASSWORD <= len(body.password) <= MAX_PASSWORD:
        raise HTTPException(
            status_code=422, detail=f"Password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters."
        )
    name = body.name.strip()[:60]

    password_hash = await asyncio.to_thread(hash_password, body.password)
    user = {
        "email": email,
        "name": name,
        "password_hash": password_hash,
        "is_demo": False,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        inserted = await db.users.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user["_id"] = inserted.inserted_id

    await start_session(response, user["_id"])
    return _public(user)


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response):
    email = body.email.strip().lower()
    if len(email) > 254 or len(body.password) > MAX_PASSWORD:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    key = _throttle_key(request, email)
    _check_throttle(key)

    user = await db.users.find_one({"email": email})
    stored = user["password_hash"] if user else DUMMY_HASH
    ok = await asyncio.to_thread(verify_password, body.password, stored)
    if not (user and ok):
        _failures.setdefault(key, []).append(time.monotonic())
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    _failures.pop(key, None)
    await start_session(response, user["_id"])
    return _public(user)


@router.post("/demo")
async def demo_login(response: Response):
    if not settings.DEMO_ACCOUNT_ENABLED:
        raise HTTPException(status_code=404, detail="Demo account is disabled.")
    user = await db.users.find_one({"email": settings.DEMO_EMAIL.lower()})
    if user is None:
        raise HTTPException(status_code=503, detail="Demo account is not ready yet, try again.")
    await start_session(response, user["_id"])
    return _public(user)


@router.post("/logout")
async def logout(request: Request, response: Response):
    await end_session(request, response)
    return {"ok": True}


@router.get("/me")
async def me(user: Dict[str, Any] = Depends(current_user)):
    return _public(user)
