from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..database import get_db
from ..models import Enrollment, InboxMessage, Lead, Mailbox, Send, Suppression
from ..render import new_tracking_id
from ..serializers import message_dict, send_dict, suppression_dict

router = APIRouter(prefix="/api", tags=["inbox"])


class MarkRead(BaseModel):
    isRead: bool = True


class ReplyBody(BaseModel):
    body: str


class SuppressionBody(BaseModel):
    email: str
    reason: str | None = "manual"


@router.get("/inbox")
async def list_inbox(unread: bool | None = None, ctx: Ctx = Depends(get_ctx),
                     db: AsyncSession = Depends(get_db)):
    q = select(InboxMessage).where(InboxMessage.workspace_id == ctx.workspace.id)
    if unread:
        q = q.where(InboxMessage.is_read == False)  # noqa: E712
    rows = (await db.execute(q.order_by(InboxMessage.received_at.desc()))).scalars().all()
    unread_count = (await db.execute(select(func.count(InboxMessage.id)).where(
        InboxMessage.workspace_id == ctx.workspace.id,
        InboxMessage.is_read == False))).scalar_one()  # noqa: E712
    return {"items": [message_dict(m) for m in rows], "total": len(rows), "unread": unread_count}


@router.patch("/inbox/{message_id}")
async def mark_read(message_id: str, body: MarkRead, ctx: Ctx = Depends(get_ctx),
                    db: AsyncSession = Depends(get_db)):
    msg = (await db.execute(select(InboxMessage).where(
        InboxMessage.id == message_id,
        InboxMessage.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    msg.is_read = body.isRead
    await db.commit()
    await db.refresh(msg)
    return {"message": message_dict(msg)}


@router.post("/inbox/{message_id}/reply")
async def reply(message_id: str, body: ReplyBody, ctx: Ctx = Depends(get_ctx),
                db: AsyncSession = Depends(get_db)):
    msg = (await db.execute(select(InboxMessage).where(
        InboxMessage.id == message_id,
        InboxMessage.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    text = (body.body or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Reply body is empty")

    now = datetime.utcnow()
    mailbox = None
    if msg.mailbox_id:
        mailbox = (await db.execute(select(Mailbox).where(
            Mailbox.id == msg.mailbox_id))).scalar_one_or_none()

    send = Send(
        workspace_id=ctx.workspace.id,
        campaign_id=msg.campaign_id,
        lead_id=msg.lead_id,
        mailbox_id=msg.mailbox_id,
        step_order=0,
        to_email=msg.from_email,
        subject=msg.subject if (msg.subject or "").lower().startswith("re:") else f"Re: {msg.subject}",
        body=text,
        status="sent",
        direction="outbound",
        tracking_id=new_tracking_id(),
        sent_at=now,
        replied_at=now,
    )
    db.add(send)
    msg.is_read = True
    if msg.lead_id:
        lead = (await db.execute(select(Lead).where(Lead.id == msg.lead_id))).scalar_one_or_none()
        if lead:
            lead.status = "replied"
        for enr in (await db.execute(select(Enrollment).where(
                Enrollment.lead_id == msg.lead_id))).scalars().all():
            enr.status = "replied"
    if mailbox:
        mailbox.sent_today = (mailbox.sent_today or 0) + 1
    await db.commit()
    await db.refresh(send)
    await db.refresh(msg)
    return {"ok": True, "send": send_dict(send), "message": message_dict(msg)}


@router.get("/suppressions")
async def list_suppressions(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Suppression).where(
        Suppression.workspace_id == ctx.workspace.id).order_by(Suppression.created_at.desc()))).scalars().all()
    items = [suppression_dict(s) for s in rows]
    return {"items": items, "total": len(items)}


@router.post("/suppressions", status_code=201)
async def add_suppression(body: SuppressionBody, ctx: Ctx = Depends(get_ctx),
                          db: AsyncSession = Depends(get_db)):
    email = (body.email or "").lower().strip()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email address is required")
    existing = (await db.execute(select(Suppression).where(
        Suppression.workspace_id == ctx.workspace.id,
        func.lower(Suppression.email) == email))).scalar_one_or_none()
    if existing:
        return {"suppression": suppression_dict(existing)}
    row = Suppression(
        workspace_id=ctx.workspace.id,
        email=email,
        reason=body.reason or "manual",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"suppression": suppression_dict(row)}


@router.delete("/suppressions/{suppression_id}")
async def delete_suppression(suppression_id: str, ctx: Ctx = Depends(get_ctx),
                             db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(Suppression).where(
        Suppression.id == suppression_id,
        Suppression.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Suppression not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True, "deleted": suppression_id}
