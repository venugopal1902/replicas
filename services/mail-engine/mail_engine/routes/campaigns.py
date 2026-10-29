from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..database import get_db
from ..models import Campaign, SequenceStep, Enrollment, Send, Lead, Mailbox
from ..render import build_html, merge_fields, render, subject_score, unknown_tags
from ..serializers import campaign_dict, lead_dict, pct, step_dict

router = APIRouter(prefix="/api", tags=["campaigns"])


class CampaignBody(BaseModel):
    name: str
    subject: str | None = ""
    body: str | None = ""
    dailyLimit: int | None = 100
    fromMailboxId: str | None = None
    stopOnReply: bool | None = True


class CampaignPatch(BaseModel):
    name: str | None = None
    subject: str | None = None
    body: str | None = None
    status: str | None = None
    dailyLimit: int | None = None
    stopOnReply: bool | None = None
    fromMailboxId: str | None = None
    trackingEnabled: bool | None = None


class StepBody(BaseModel):
    stepOrder: int | None = None
    delayDays: int | None = 0
    subject: str = ""
    body: str = ""


class StepPatch(BaseModel):
    stepOrder: int | None = None
    delayDays: int | None = None
    subject: str | None = None
    body: str | None = None


class PreviewBody(BaseModel):
    leadId: str | None = None


async def campaign_stats(campaign_id: str, db: AsyncSession) -> dict:
    sent = (await db.execute(select(func.count(Send.id)).where(
        Send.campaign_id == campaign_id, Send.status == "sent"))).scalar_one()
    opened = (await db.execute(select(func.count(Send.id)).where(
        Send.campaign_id == campaign_id, Send.opened_at.isnot(None)))).scalar_one()
    clicked = (await db.execute(select(func.count(Send.id)).where(
        Send.campaign_id == campaign_id, Send.clicked_at.isnot(None)))).scalar_one()
    replied = (await db.execute(select(func.count(Send.id)).where(
        Send.campaign_id == campaign_id, Send.replied_at.isnot(None)))).scalar_one()
    bounced = (await db.execute(select(func.count(Send.id)).where(
        Send.campaign_id == campaign_id, Send.status == "bounced"))).scalar_one()
    enrolled = (await db.execute(select(func.count(Enrollment.id)).where(
        Enrollment.campaign_id == campaign_id))).scalar_one()
    steps = (await db.execute(select(func.count(SequenceStep.id)).where(
        SequenceStep.campaign_id == campaign_id))).scalar_one()
    return {
        "enrolled": enrolled, "sent": sent, "opened": opened, "clicked": clicked,
        "replied": replied, "bounced": bounced, "steps": steps,
        "openRate": pct(opened, sent), "clickRate": pct(clicked, sent),
        "replyRate": pct(replied, sent),
    }


async def _get_campaign(cid: str, ctx: Ctx, db: AsyncSession) -> Campaign:
    c = (await db.execute(select(Campaign).where(
        Campaign.id == cid, Campaign.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


@router.get("/campaigns")
async def list_campaigns(status: str | None = None, ctx: Ctx = Depends(get_ctx),
                         db: AsyncSession = Depends(get_db)):
    q = select(Campaign).where(Campaign.workspace_id == ctx.workspace.id)
    if status:
        q = q.where(Campaign.status == status)
    rows = (await db.execute(q.order_by(Campaign.created_at.desc()))).scalars().all()
    items = [campaign_dict(c, await campaign_stats(c.id, db)) for c in rows]
    return {"items": items, "total": len(items)}


@router.post("/campaigns", status_code=201)
async def create_campaign(body: CampaignBody, ctx: Ctx = Depends(get_ctx),
                          db: AsyncSession = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Campaign name is required")
    c = Campaign(
        workspace_id=ctx.workspace.id,
        name=name,
        subject=body.subject or "",
        body=body.body or "",
        daily_limit=max(1, body.dailyLimit or 100),
        stop_on_reply=True if body.stopOnReply is None else body.stopOnReply,
        from_mailbox_id=body.fromMailboxId,
        status="draft",
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)

    # Every campaign needs at least a first step so sending works immediately.
    if c.subject or c.body:
        db.add(SequenceStep(
            campaign_id=c.id, step_order=1, delay_days=0,
            subject=c.subject or "", body=c.body or "",
        ))
        await db.commit()
    return {"campaign": campaign_dict(c, await campaign_stats(c.id, db))}


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, ctx: Ctx = Depends(get_ctx),
                       db: AsyncSession = Depends(get_db)):
    c = await _get_campaign(campaign_id, ctx, db)
    steps = (await db.execute(select(SequenceStep).where(
        SequenceStep.campaign_id == c.id).order_by(SequenceStep.step_order))).scalars().all()
    return {
        "campaign": campaign_dict(c, await campaign_stats(c.id, db)),
        "steps": [step_dict(s) for s in steps],
    }


@router.patch("/campaigns/{campaign_id}")
async def patch_campaign(campaign_id: str, body: CampaignPatch, ctx: Ctx = Depends(get_ctx),
                         db: AsyncSession = Depends(get_db)):
    c = await _get_campaign(campaign_id, ctx, db)
    if body.name is not None:
        c.name = body.name.strip() or c.name
    if body.subject is not None:
        c.subject = body.subject
    if body.body is not None:
        c.body = body.body
    if body.status is not None:
        if body.status not in ("draft", "active", "paused", "completed"):
            raise HTTPException(status_code=400,
                                detail="status must be draft, active, paused or completed")
        c.status = body.status
        if body.status == "active" and not c.activated_at:
            c.activated_at = datetime.utcnow()
    if body.dailyLimit is not None:
        c.daily_limit = max(1, body.dailyLimit)
    if body.stopOnReply is not None:
        c.stop_on_reply = body.stopOnReply
    if body.trackingEnabled is not None:
        c.tracking_enabled = body.trackingEnabled
    if body.fromMailboxId is not None:
        mb = (await db.execute(select(Mailbox).where(
            Mailbox.id == body.fromMailboxId,
            Mailbox.workspace_id == ctx.workspace.id))).scalar_one_or_none()
        if not mb:
            raise HTTPException(status_code=400, detail="fromMailboxId does not exist")
        c.from_mailbox_id = mb.id
    await db.commit()
    await db.refresh(c)
    return {"campaign": campaign_dict(c, await campaign_stats(c.id, db))}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str, ctx: Ctx = Depends(get_ctx),
                          db: AsyncSession = Depends(get_db)):
    c = await _get_campaign(campaign_id, ctx, db)
    for model in (SequenceStep, Enrollment):
        rows = (await db.execute(select(model).where(model.campaign_id == c.id))).scalars().all()
        for row in rows:
            await db.delete(row)
    await db.delete(c)
    await db.commit()
    return {"ok": True, "deleted": campaign_id}


@router.post("/campaigns/{campaign_id}/activate")
async def activate_campaign(campaign_id: str, ctx: Ctx = Depends(get_ctx),
                            db: AsyncSession = Depends(get_db)):
    c = await _get_campaign(campaign_id, ctx, db)
    if not c.activated_at:
        c.activated_at = datetime.utcnow()
    c.status = "active"
    await db.commit()
    await db.refresh(c)
    return {"campaign": campaign_dict(c, await campaign_stats(c.id, db))}


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: str, ctx: Ctx = Depends(get_ctx),
                         db: AsyncSession = Depends(get_db)):
    c = await _get_campaign(campaign_id, ctx, db)
    c.status = "paused"
    await db.commit()
    await db.refresh(c)
    return {"campaign": campaign_dict(c, await campaign_stats(c.id, db))}


@router.post("/campaigns/{campaign_id}/preview")
async def preview_campaign(campaign_id: str, body: PreviewBody, ctx: Ctx = Depends(get_ctx),
                           db: AsyncSession = Depends(get_db)):
    c = await _get_campaign(campaign_id, ctx, db)
    lead = None
    if body.leadId:
        lead = (await db.execute(select(Lead).where(
            Lead.id == body.leadId, Lead.workspace_id == ctx.workspace.id))).scalar_one_or_none()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
    if not lead:
        lead = (await db.execute(select(Lead).where(
            Lead.workspace_id == ctx.workspace.id).limit(1))).scalars().first()

    if lead:
        fields = merge_fields({
            "first_name": lead.first_name, "last_name": lead.last_name,
            "company": lead.company, "title": lead.title, "email": lead.email,
            "domain": lead.domain,
        })
    else:
        fields = merge_fields()

    subject = render(c.subject, fields)
    text = render(c.body, fields)
    return {
        "subject": subject,
        "body": text,
        "html": build_html(text, unsubscribe_email=lead.email if lead else None),
        "unknownTags": unknown_tags(c.subject) + unknown_tags(c.body),
        "subjectScore": subject_score(c.subject),
        "lead": lead_dict(lead) if lead else None,
    }


@router.get("/campaigns/{campaign_id}/steps")
async def list_steps(campaign_id: str, ctx: Ctx = Depends(get_ctx),
                     db: AsyncSession = Depends(get_db)):
    c = await _get_campaign(campaign_id, ctx, db)
    rows = (await db.execute(select(SequenceStep).where(
        SequenceStep.campaign_id == c.id).order_by(SequenceStep.step_order))).scalars().all()
    items = [step_dict(s) for s in rows]
    return {"items": items, "total": len(items)}


@router.post("/campaigns/{campaign_id}/steps", status_code=201)
async def create_step(campaign_id: str, body: StepBody, ctx: Ctx = Depends(get_ctx),
                      db: AsyncSession = Depends(get_db)):
    c = await _get_campaign(campaign_id, ctx, db)
    if not (body.subject or "").strip() and not (body.body or "").strip():
        raise HTTPException(status_code=400, detail="A step needs a subject or a body")
    order = body.stepOrder
    if order is None:
        existing = (await db.execute(select(SequenceStep).where(
            SequenceStep.campaign_id == c.id))).scalars().all()
        order = max([s.step_order for s in existing], default=0) + 1
    step = SequenceStep(
        campaign_id=c.id,
        step_order=order,
        delay_days=max(0, body.delayDays or 0),
        subject=body.subject or "",
        body=body.body or "",
    )
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return {"step": step_dict(step)}


@router.patch("/steps/{step_id}")
async def patch_step(step_id: str, body: StepPatch, ctx: Ctx = Depends(get_ctx),
                     db: AsyncSession = Depends(get_db)):
    step = (await db.execute(select(SequenceStep).where(SequenceStep.id == step_id))).scalar_one_or_none()
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    await _get_campaign(step.campaign_id, ctx, db)  # ownership check
    if body.stepOrder is not None:
        step.step_order = body.stepOrder
    if body.delayDays is not None:
        step.delay_days = max(0, body.delayDays)
    if body.subject is not None:
        step.subject = body.subject
    if body.body is not None:
        step.body = body.body
    await db.commit()
    await db.refresh(step)
    return {"step": step_dict(step)}


@router.delete("/steps/{step_id}")
async def delete_step(step_id: str, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    step = (await db.execute(select(SequenceStep).where(SequenceStep.id == step_id))).scalar_one_or_none()
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    await _get_campaign(step.campaign_id, ctx, db)
    await db.delete(step)
    await db.commit()
    return {"ok": True, "deleted": step_id}
