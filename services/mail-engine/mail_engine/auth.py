"""Password hashing, sessions and request context."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import User, Workspace, Session

PBKDF_ROUNDS = 240_000


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF_ROUNDS)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, digest = stored.split("$", 2)
    except (ValueError, AttributeError):
        return False
    if algo != "pbkdf2":
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF_ROUNDS).hex()
    return hmac.compare_digest(check, digest)


def new_token(prefix: str = "sess") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


class Ctx:
    def __init__(self, user: User, workspace: Workspace, via: str = "session"):
        self.user = user
        self.workspace = workspace
        self.via = via


async def _ctx_from_session(token: str, db: AsyncSession) -> Optional[Ctx]:
    sess = (await db.execute(select(Session).where(Session.token == token))).scalar_one_or_none()
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
    db: AsyncSession = Depends(get_db),
) -> Ctx:
    """Accept `Authorization: Bearer <token>` or the `mail_session` cookie."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        token = request.cookies.get("mail_session")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

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
