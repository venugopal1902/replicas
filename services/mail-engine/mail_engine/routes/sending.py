from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..config import settings
from ..database import get_db
from ..models import (
    Campaign, Enrollment, InboxMessage, Lead, Mailbox, Send, SequenceStep, Suppression,
)
from ..render import PIXEL_GIF, merge_fields, new_tracking_id, render, verify_unsubscribe_token
from ..serializers import send_dict

router = APIRouter(prefix="/api", tags=["sending"])


class EnrollBody(BaseModel):
    leadIds: list[str] | None = None
    all: bool | None = False


class SendNextBody(BaseModel):
    limit: int | None = 25


async def _campaign(cid: str, ctx: Ctx, db: AsyncSession) -> Campaign:
    c = (await db.execute(select(Campaign).where(
        Campaign.id == cid, Campaign.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


async def _suppressed(ws_id: str, email: str, db: AsyncSession) -> bool:
    row = (await db.execute(select(Suppression).where(
        Suppression.workspace_id == ws_id,
        func.lower(Suppression.email) == email.lower()))).scalar_one_or_none()
    return row is not None


def _reset_mailbox_day(mb: Mailbox) -> None:
    if mb.last_reset != date.today():
        mb.sent_today = 0
        mb.last_reset = date.today()


@router.post("/campaigns/{campaign_id}/enroll")
async def enroll(campaign_id: str, body: EnrollBody, ctx: Ctx = Depends(get_ctx),
                 db: AsyncSession = Depends(get_db)):
    c = await _campaign(campaign_id, ctx, db)

    q = select(Lead).where(Lead.workspace_id == ctx.workspace.id)
    if not body.all:
        ids = body.leadIds or []
        if not ids:
            raise HTTPException(status_code=400, detail="Provide leadIds or set all=true")
        q = q.where(Lead.id.in_(ids))
    leads = (await db.execute(q)).scalars().all()

    existing = {(e.lead_id) for e in (await db.execute(select(Enrollment).where(
        Enrollment.campaign_id == c.id))).scalars().all()}
    suppressed = {row.lower() for row in (await db.execute(select(Suppression.email).where(
        Suppression.workspace_id == ctx.workspace.id))).scalars().all()}

    enrolled = skipped = suppressed_count = already = 0
    for lead in leads:
        if lead.email.lower() in suppressed:
            suppressed_count += 1
            skipped += 1
            continue
        if lead.id in existing:
            already += 1
            skipped += 1
            continue
        db.add(Enrollment(
            campaign_id=c.id,
            lead_id=lead.id,
            status="active",
            current_step=0,
            next_send_at=datetime.utcnow(),
        ))
        existing.add(lead.id)
        enrolled += 1
    await db.commit()
    return {
        "enrolled": enrolled,
        "skipped": skipped,
        "reasons": {"suppressed": suppressed_count, "already_enrolled": already},
    }


@router.get("/campaigns/{campaign_id}/enrollments")
async def list_enrollments(campaign_id: str, ctx: Ctx = Depends(get_ctx),
                           db: AsyncSession = Depends(get_db)):
    c = await _campaign(campaign_id, ctx, db)
    rows = (await db.execute(select(Enrollment).where(
        Enrollment.campaign_id == c.id).order_by(Enrollment.created_at))).scalars().all()
    lead_ids = [r.lead_id for r in rows]
    leads = {}
    if lead_ids:
        for lead in (await db.execute(select(Lead).where(Lead.id.in_(lead_ids)))).scalars().all():
            leads[lead.id] = lead
    items = []
    for r in rows:
        lead = leads.get(r.lead_id)
        items.append({
            "id": r.id,
            "leadId": r.lead_id,
            "email": lead.email if lead else None,
            "name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else None,
            "company": lead.company if lead else None,
            "status": r.status,
            "currentStep": r.current_step,
            "nextSendAt": r.next_send_at.isoformat(timespec="seconds") if r.next_send_at else None,
            "lastSentAt": r.last_sent_at.isoformat(timespec="seconds") if r.last_sent_at else None,
        })
    return {"items": items, "total": len(items)}


@router.post("/campaigns/{campaign_id}/send-next")
async def send_next(campaign_id: str, body: SendNextBody | None = None,
                    ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    c = await _campaign(campaign_id, ctx, db)
    if c.status not in ("active", "paused"):
        raise HTTPException(status_code=409,
                            detail="Activate the campaign before sending (status is %s)" % c.status)
    limit = max(1, min((body.limit if body else 25) or 25, 200))

    steps = (await db.execute(select(SequenceStep).where(
        SequenceStep.campaign_id == c.id).order_by(SequenceStep.step_order))).scalars().all()
    if not steps:
        raise HTTPException(status_code=409, detail="Campaign has no sequence steps")

    now = datetime.utcnow()
    today_start = datetime.combine(date.today(), datetime.min.time())

    sent_today_campaign = (await db.execute(select(func.count(Send.id)).where(
        Send.campaign_id == c.id, Send.status == "sent", Send.sent_at >= today_start))).scalar_one()

    mailbox = None
    if c.from_mailbox_id:
        mailbox = (await db.execute(select(Mailbox).where(
            Mailbox.id == c.from_mailbox_id))).scalar_one_or_none()
    if not mailbox:
        mailbox = (await db.execute(select(Mailbox).where(
            Mailbox.workspace_id == ctx.workspace.id,
            Mailbox.status == "active").order_by(Mailbox.warmup_score.desc()))).scalars().first()
    if mailbox:
        _reset_mailbox_day(mailbox)

    enrollments = (await db.execute(select(Enrollment).where(
        Enrollment.campaign_id == c.id,
        Enrollment.status == "active",
        Enrollment.next_send_at <= now,
    ).order_by(Enrollment.next_send_at).limit(limit))).scalars().all()

    sent, blocked, finished = 0, 0, 0
    skipped = {"suppressed": 0, "paused": 0}
    created: list[dict] = []
    touches = 0

    for enr in enrollments:
        lead = (await db.execute(select(Lead).where(Lead.id == enr.lead_id))).scalar_one_or_none()
        if not lead:
            enr.status = "paused"
            continue
        if await _suppressed(ctx.workspace.id, lead.email, db):
            enr.status = "paused"
            blocked += 1
            skipped["suppressed"] += 1
            lead.status = "unsubscribed"
            continue
        if len(steps) <= enr.current_step:
            enr.status = "finished"
            finished += 1
            continue
        if (c.daily_limit and sent_today_campaign + sent >= c.daily_limit):
            blocked += 1
            skipped["paused"] += 1
            break
        if not mailbox or mailbox.status != "active":
            blocked += 1
            skipped["paused"] += 1
            break
        if mailbox.sent_today + touches >= (mailbox.daily_limit or 0):
            blocked += 1
            skipped["paused"] += 1
            break

        step = steps[enr.current_step]
        fields = merge_fields({
            "first_name": lead.first_name, "last_name": lead.last_name,
            "company": lead.company, "title": lead.title, "email": lead.email,
            "domain": lead.domain,
        })
        subject = render(step.subject or c.subject, fields)
        text = render(step.body or c.body, fields)
        tracking_id = new_tracking_id()

        db.add(Send(
            workspace_id=ctx.workspace.id,
            campaign_id=c.id,
            lead_id=lead.id,
            mailbox_id=mailbox.id,
            step_order=step.step_order,
            to_email=lead.email,
            subject=subject,
            body=text,
            status="sent",
            tracking_id=tracking_id,
            sent_at=now,
        ))
        touches += 1
        sent += 1
        created.append({
            "to": lead.email, "step": step.step_order,
            "subject": subject, "trackingId": tracking_id,
        })

        enr.current_step = (enr.current_step or 0) + 1
        enr.last_sent_at = now
        lead.status = "contacted" if lead.status in ("new", "opened") else lead.status

        if enr.current_step >= len(steps):
            enr.status = "finished"
            enr.next_send_at = None
            finished += 1
        else:
            next_step = steps[enr.current_step]
            delay = next_step.delay_days or 0
            enr.next_send_at = now + timedelta(days=delay)

    if mailbox:
        mailbox.sent_today = (mailbox.sent_today or 0) + touches

    await db.commit()

    # Re-read the rows so the response carries real ids.
    rows = (await db.execute(select(Send).where(
        Send.campaign_id == c.id).order_by(Send.sent_at.desc()).limit(len(created) if created else 1))).scalars().all()
    by_tracking = {r.tracking_id: r for r in rows}
    out = []
    for item in created:
        row = by_tracking.get(item["trackingId"])
        out.append({
            "id": row.id if row else None,
            **item,
            "status": row.status if row else "sent",
        })

    return {
        "sent": sent,
        "blocked": blocked,
        "finished": finished,
        "skipped": skipped,
        "sends": out,
    }


@router.get("/sends")
async def list_sends(campaignId: str | None = None, limit: int = 100,
                     ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    q = select(Send).where(Send.workspace_id == ctx.workspace.id)
    if campaignId:
        q = q.where(Send.campaign_id == campaignId)
    rows = (await db.execute(q.order_by(Send.sent_at.desc()).limit(max(1, min(limit, 500))))).scalars().all()
    items = [send_dict(s) for s in rows]
    return {"items": items, "total": len(items)}


@router.get("/tracking/open/{tracking_id}.gif")
async def track_open(tracking_id: str, db: AsyncSession = Depends(get_db)):
    send = (await db.execute(select(Send).where(Send.tracking_id == tracking_id))).scalar_one_or_none()
    if send and not send.opened_at:
        send.opened_at = datetime.utcnow()
        lead = (await db.execute(select(Lead).where(Lead.id == send.lead_id))).scalar_one_or_none()
        if lead and lead.status in ("new", "contacted"):
            lead.status = "opened"
        await db.commit()
    return Response(content=PIXEL_GIF, media_type="image/gif",
                    headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/tracking/click/{tracking_id}")
async def track_click(tracking_id: str, db: AsyncSession = Depends(get_db)):
    send = (await db.execute(select(Send).where(Send.tracking_id == tracking_id))).scalar_one_or_none()
    if send:
        if not send.clicked_at:
            send.clicked_at = datetime.utcnow()
            if not send.opened_at:
                send.opened_at = send.clicked_at
        await db.commit()
    return RedirectResponse(url=settings.PUBLIC_BASE_URL, status_code=302)


async def _apply_unsubscribe(email: str, reason: str, db: AsyncSession) -> bool:
    email = email.lower().strip()
    existing = (await db.execute(select(Suppression).where(
        func.lower(Suppression.email) == email))).scalars().first()
    if not existing:
        lead = (await db.execute(select(Lead).where(func.lower(Lead.email) == email))).scalars().first()
        db.add(Suppression(
            workspace_id=lead.workspace_id if lead else "",
            email=email,
            reason=reason,
        ))
        if lead:
            lead.status = "unsubscribed"
            for enr in (await db.execute(select(Enrollment).where(
                    Enrollment.lead_id == lead.id))).scalars().all():
                enr.status = "paused"
    await db.commit()
    return True


@router.post("/unsubscribe/{token}")
async def unsubscribe(token: str, email: str = Query(...), db: AsyncSession = Depends(get_db)):
    if not verify_unsubscribe_token(email, token):
        raise HTTPException(status_code=400, detail="Invalid unsubscribe token")
    await _apply_unsubscribe(email, "unsubscribe", db)
    return {"ok": True, "email": email.lower(), "suppressed": True}


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe_page(token: str, email: str = Query(...), db: AsyncSession = Depends(get_db)):
    if not verify_unsubscribe_token(email, token):
        return HTMLResponse("<h1>Invalid link</h1>", status_code=400)
    await _apply_unsubscribe(email, "unsubscribe", db)
    return HTMLResponse(
        "<html><body style='font-family:Arial;padding:40px'>"
        f"<h1>Unsubscribed</h1><p>{email.lower()} will not receive more email from this workspace.</p>"
        "</body></html>"
    )
