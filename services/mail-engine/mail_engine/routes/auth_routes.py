from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, create_session, get_ctx, verify_password
from ..database import get_db
from ..models import User, Workspace, Session, Mailbox, Campaign, Lead, Send, InboxMessage
from ..serializers import workspace_dict

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "mail_session"


class LoginBody(BaseModel):
    email: str
    password: str


async def workspace_stats(ws_id: str, db: AsyncSession) -> dict:
    mailboxes = (await db.execute(
        select(func.count(Mailbox.id)).where(Mailbox.workspace_id == ws_id)
    )).scalar_one()
    active_campaigns = (await db.execute(
        select(func.count(Campaign.id)).where(
            Campaign.workspace_id == ws_id, Campaign.status == "active")
    )).scalar_one()
    leads = (await db.execute(
        select(func.count(Lead.id)).where(Lead.workspace_id == ws_id)
    )).scalar_one()
    replies = (await db.execute(
        select(func.count(Send.id)).where(
            Send.workspace_id == ws_id, Send.replied_at.isnot(None))
    )).scalar_one()
    unread = (await db.execute(
        select(func.count(InboxMessage.id)).where(
            InboxMessage.workspace_id == ws_id, InboxMessage.is_read == False)  # noqa: E712
    )).scalar_one()
    return {
        "mailboxes": mailboxes,
        "activeCampaigns": active_campaigns,
        "leads": leads,
        "sentToday": 0,
        "replies": replies,
        "unread": unread,
    }


def _user(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name}


@router.post("/login")
async def login(body: LoginBody, response: Response, db: AsyncSession = Depends(get_db)):
    email = body.email.lower().strip()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    ws = (await db.execute(
        select(Workspace).where(Workspace.owner_id == user.id)
    )).scalars().first()
    if not ws:
        raise HTTPException(status_code=500, detail="Workspace missing for this account")

    token = await create_session(user, ws, db)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return {
        "token": token,
        "user": _user(user),
        "workspace": workspace_dict(ws, await workspace_stats(ws.id, db)),
    }


@router.post("/logout")
async def logout(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    sess = (await db.execute(select(Session).where(Session.user_id == ctx.user.id))).scalars().all()
    for row in sess:
        await db.delete(row)
    await db.commit()
    return {"ok": True}


@router.get("/me")
async def me(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    return {
        "user": _user(ctx.user),
        "workspace": workspace_dict(ctx.workspace, await workspace_stats(ctx.workspace.id, db)),
        "via": ctx.via,
    }
