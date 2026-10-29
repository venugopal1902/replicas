"""Password hashing, sessions, API keys and request context."""
import hashlib
import hmac
import secrets
from datetime import datetime, date, timedelta
from typing import Optional, Tuple

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_db
from .models import User, Workspace, WorkspaceMember, Session, ApiKey

PBKDF_ROUNDS = 240_000


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF_ROUNDS)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if algo != "pbkdf2":
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF_ROUNDS).hex()
    return hmac.compare_digest(check, digest)


def new_token(prefix: str = "sess") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


class Ctx:
    """Everything a route needs about the caller."""

    def __init__(self, user: User, workspace: Workspace, via: str, api_key: Optional[ApiKey] = None):
        self.user = user
        self.workspace = workspace
        self.via = via              # session | api_key
        self.api_key = api_key


async def _ctx_from_api_key(raw: str, db: AsyncSession) -> Optional[Ctx]:
    res = await db.execute(select(ApiKey).where(ApiKey.key == raw, ApiKey.is_active == True))  # noqa: E712
    key = res.scalar_one_or_none()
    if not key:
        return None

    today = date.today()
    if key.last_reset != today:
        key.calls_today = 0
        key.last_reset = today
    key.calls_today = (key.calls_today or 0) + 1
    if key.calls_limit and key.calls_today > key.calls_limit:
        raise HTTPException(status_code=429, detail="Daily API call limit reached for this key")

    ws = (await db.execute(select(Workspace).where(Workspace.id == key.workspace_id))).scalar_one_or_none()
    user = (await db.execute(select(User).where(User.id == key.user_id))).scalar_one_or_none() if key.user_id else None
    if not ws or not user:
        return None
    await db.commit()
    return Ctx(user, ws, "api_key", key)


async def _ctx_from_session(token: str, db: AsyncSession) -> Optional[Ctx]:
    res = await db.execute(select(Session).where(Session.token == token))
    sess = res.scalar_one_or_none()
    if not sess:
        return None
    if sess.expires_at and sess.expires_at < datetime.utcnow():
        return None
    user = (await db.execute(select(User).where(User.id == sess.user_id))).scalar_one_or_none()
    ws = (await db.execute(select(Workspace).where(Workspace.id == sess.workspace_id))).scalar_one_or_none()
    if not user or not ws:
        return None
    return Ctx(user, ws, "session")


async def get_ctx(
    request: Request,
    authorization: str = Header(None),
    x_api_key: str = Header(None, alias="x-api-key"),
    db: AsyncSession = Depends(get_db),
) -> Ctx:
    """Accept either `Authorization: Bearer <session>` or `x-api-key: <key>`."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key.strip()
    if not token:
        token = request.cookies.get("lead_session")

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    ctx = await _ctx_from_api_key(token, db)
    if not ctx:
        ctx = await _ctx_from_session(token, db)
    if not ctx:
        raise HTTPException(status_code=401, detail="Invalid or expired credentials")
    return ctx


async def create_session(user: User, workspace: Workspace, db: AsyncSession, days: int = 30) -> str:
    token = new_token("sess")
    db.add(Session(
        token=token,
        user_id=user.id,
        workspace_id=workspace.id,
        expires_at=datetime.utcnow() + timedelta(days=days),
    ))
    await db.commit()
    return token
