"""Startup work for accounts: indexes, the demo user, and adopting pre-login history."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.app.auth.security import hash_password
from backend.app.core.config import settings
from backend.app.db.mongo import db


async def prepare_accounts() -> None:
    await db.users.create_index("email", unique=True)
    await db.sessions.create_index("token_hash", unique=True)
    await db.sessions.create_index("expires_at", expireAfterSeconds=0)  # Mongo purges expired sessions

    if not settings.DEMO_ACCOUNT_ENABLED:
        return

    email = settings.DEMO_EMAIL.lower()
    demo = await db.users.find_one({"email": email})
    if demo is None:
        password_hash = await asyncio.to_thread(hash_password, settings.DEMO_PASSWORD)
        inserted = await db.users.insert_one({
            "email": email,
            "name": "Demo User",
            "password_hash": password_hash,
            "is_demo": True,
            "created_at": datetime.now(timezone.utc),
        })
        demo_id = inserted.inserted_id
    else:
        demo_id = demo["_id"]

    # Runs created before login existed belong to the demo account, so that
    # history is still visible after signing in as the demo user.
    await db.tasks.update_many({"user_id": {"$exists": False}}, {"$set": {"user_id": demo_id}})
