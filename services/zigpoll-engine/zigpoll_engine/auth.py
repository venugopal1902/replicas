import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_db
from .models import AdminSession, AdminUser, utc_now

PBKDF_ROUNDS = 240_000


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF_ROUNDS)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2":
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF_ROUNDS).hex()
    return hmac.compare_digest(check, digest)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AdminContext:
    user: AdminUser


async def get_bearer(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer authentication required")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer authentication required")
    return token


async def get_admin_ctx(token: str = Depends(get_bearer), db: AsyncSession = Depends(get_db)) -> AdminContext:
    if not token.startswith("zpa_"):
        raise HTTPException(status_code=401, detail="Invalid admin token")
    session = (await db.execute(select(AdminSession).where(
        AdminSession.token_hash == token_hash(token), AdminSession.expires_at > utc_now()
    ))).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")
    user = (await db.execute(select(AdminUser).where(
        AdminUser.id == session.user_id, AdminUser.is_active.is_(True)
    ))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")
    return AdminContext(user=user)


async def create_admin_session(user: AdminUser, db: AsyncSession) -> str:
    token = "zpa_" + secrets.token_urlsafe(32)
    db.add(AdminSession(user_id=user.id, token_hash=token_hash(token),
                        expires_at=utc_now() + timedelta(hours=settings.SESSION_HOURS)))
    await db.commit()
    return token
