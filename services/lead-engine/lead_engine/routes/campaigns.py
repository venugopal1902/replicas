"""Email campaigns: CRUD, steps, recipients, simulated send, open/click tracking.

This module never sends email. Sending is simulated: it only records the intent
as a CampaignEvent and advances the recipient's counters.
"""
import secrets
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Break circular dependency between auth and database if neither is imported yet
import sys
if "lead_engine.auth" not in sys.modules:
    import types
    _stub = types.ModuleType("lead_engine.auth")
    _stub.hash_password = lambda p, s=None: ""
    sys.modules["lead_engine.auth"] = _stub
    from .. import database as _db
    del sys.modules["lead_engine.auth"]
    from .. import auth as _auth
    _db.hash_password = _auth.hash_password

from ..auth import Ctx, get_ctx
from ..database import get_db
from ..models import (
    Campaign, CampaignEvent, CampaignRecipient, CampaignStep, Contact, Workspace,
)

router = APIRouter(prefix="/api", tags=["campaigns"])
public_router = APIRouter(tags=["tracking"])

STATUSES = {"draft", "active", "paused", "done"}
MAX_STEPS = 10

# Minimal 1x1 transparent GIF, 43 bytes, generated in code.
_GIF = bytearray(b"GIF89a" + b"\x01\x00\x01\x00" + b"\x80\x00\x00" + b"\x00\x00\x00"
                 + b"\xff\xff\xff" + b"!\xf9\x04" + b"\x01\x00\x00\x00\x00" + b",")
_GIF += b"\x00\x00\x00\x00" + b"\x01\x00\x01\x00" + b"\x00" + b"\x02\x02" + b"D\x01" + b"\x00" + b";"
TRANSPARENT_GIF = bytes(_GIF)
assert len(TRANSPARENT_GIF) == 43

NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


class CampaignCreate(BaseModel):
    name: str
    fromEmail: Optional[str] = None
    fromName: Optional[str] = None


class CampaignPatch(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    fromEmail: Optional[str] = None
    fromName: Optional[str] = None
    dailyLimit: Optional[int] = None


class StepCreate(BaseModel):
    subject: str
    body: str
    delayDays: Optional[int] = 0


class RecipientsBody(BaseModel):
    contactIds: list[str]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _campaign_dict(c: Campaign) -> dict:
    return {
        "id": c.id,
        "workspaceId": c.workspace_id,
        "name": c.name,
        "status": c.status,
        "fromEmail": c.from_email,
        "fromName": c.from_name,
        "dailyLimit": c.daily_limit,
        "createdAt": _iso(c.created_at),
    }


def _step_dict(s: CampaignStep) -> dict:
    return {
        "id": s.id,
        "position": s.position,
        "subject": s.subject,
        "body": s.body,
        "delayDays": s.delay_days,
    }


async def _load_campaign(db: AsyncSession, campaign_id: str, workspace_id: str) -> Campaign:
    c = (await db.execute(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.workspace_id == workspace_id)
    )).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


async def _stats_for(db: AsyncSession, campaign_ids: list[str]) -> dict[str, dict]:
    """One grouped query covering every campaign in the list."""
    base = {
        "recipients": 0, "sent": 0, "opened": 0, "clicked": 0, "replied": 0, "bounced": 0,
    }
    out = {cid: dict(base) for cid in campaign_ids}
    if not campaign_ids:
        return out

    rows = (await db.execute(
        select(
            CampaignRecipient.campaign_id,
            func.count(CampaignRecipient.id),
            func.coalesce(func.sum(CampaignRecipient.sent_count), 0),
            func.coalesce(func.sum(CampaignRecipient.open_count), 0),
            func.coalesce(func.sum(CampaignRecipient.click_count), 0),
            func.coalesce(func.sum(
                case((CampaignRecipient.status == "replied", 1), else_=0)
            ), 0),
            func.coalesce(func.sum(
                case((CampaignRecipient.status == "bounced", 1), else_=0)
            ), 0),
        ).where(CampaignRecipient.campaign_id.in_(campaign_ids))
        .group_by(CampaignRecipient.campaign_id)
    )).all()
    for cid, recipients, sent, opened, clicked, replied, bounced in rows:
        out[cid] = {
            "recipients": int(recipients or 0),
            "sent": int(sent or 0),
            "opened": int(opened or 0),
            "clicked": int(clicked or 0),
            "replied": int(replied or 0),
            "bounced": int(bounced or 0),
        }
    return out


async def _step_counts(db: AsyncSession, campaign_ids: list[str]) -> dict[str, int]:
    out = {cid: 0 for cid in campaign_ids}
    if not campaign_ids:
        return out
    rows = (await db.execute(
        select(CampaignStep.campaign_id, func.count(CampaignStep.id))
        .where(CampaignStep.campaign_id.in_(campaign_ids))
        .group_by(CampaignStep.campaign_id)
    )).all()
    for cid, n in rows:
        out[cid] = int(n or 0)
    return out


# --- campaigns ----------------------------------------------------------------

@router.get("/campaigns")
async def list_campaigns(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    campaigns = (await db.execute(
        select(Campaign).where(Campaign.workspace_id == ctx.workspace.id)
        .order_by(Campaign.created_at.desc())
    )).scalars().all()
    ids = [c.id for c in campaigns]
    stats = await _stats_for(db, ids)
    steps = await _step_counts(db, ids)
    items = []
    for c in campaigns:
        s = stats[c.id]
        items.append({
            "id": c.id,
            "name": c.name,
            "status": c.status,
            "fromEmail": c.from_email,
            "stepCount": steps[c.id],
            "recipients": s["recipients"],
            "sent": s["sent"],
            "opened": s["opened"],
            "clicked": s["clicked"],
            "replied": s["replied"],
            "bounced": s["bounced"],
            "createdAt": _iso(c.created_at),
        })
    return {"items": items}


@router.post("/campaigns")
async def create_campaign(body: CampaignCreate, ctx: Ctx = Depends(get_ctx),
                          db: AsyncSession = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Campaign name is required")
    c = Campaign(
        workspace_id=ctx.workspace.id,
        name=name[:200],
        status="draft",
        from_email=body.fromEmail,
        from_name=body.fromName,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return _campaign_dict(c)


@router.get("/campaigns/{id}")
async def get_campaign(id: str, ctx: Ctx = Depends(get_ctx),
                       db: AsyncSession = Depends(get_db)):
    c = await _load_campaign(db, id, ctx.workspace.id)
    steps = (await db.execute(
        select(CampaignStep).where(CampaignStep.campaign_id == c.id)
        .order_by(CampaignStep.position)
    )).scalars().all()
    stats = (await _stats_for(db, [c.id]))[c.id]
    return {"campaign": _campaign_dict(c), "steps": [_step_dict(s) for s in steps], "stats": stats}


@router.patch("/campaigns/{id}")
async def patch_campaign(id: str, body: CampaignPatch, ctx: Ctx = Depends(get_ctx),
                         db: AsyncSession = Depends(get_db)):
    c = await _load_campaign(db, id, ctx.workspace.id)
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        if data["status"] not in STATUSES:
            raise HTTPException(status_code=400, detail="status must be one of draft|active|paused|done")
        c.status = data["status"]
    if "name" in data and data["name"] is not None:
        name = data["name"].strip()
        if not name:
            raise HTTPException(status_code=400, detail="Campaign name is required")
        c.name = name[:200]
    if "fromEmail" in data:
        c.from_email = data["fromEmail"]
    if "fromName" in data:
        c.from_name = data["fromName"]
    if "dailyLimit" in data and data["dailyLimit"] is not None:
        if data["dailyLimit"] < 1:
            raise HTTPException(status_code=400, detail="dailyLimit must be at least 1")
        c.daily_limit = data["dailyLimit"]
    await db.commit()
    await db.refresh(c)
    return _campaign_dict(c)


@router.delete("/campaigns/{id}")
async def delete_campaign(id: str, ctx: Ctx = Depends(get_ctx),
                          db: AsyncSession = Depends(get_db)):
    c = await _load_campaign(db, id, ctx.workspace.id)
    recipient_ids = (await db.execute(
        select(CampaignRecipient.id).where(CampaignRecipient.campaign_id == c.id)
    )).scalars().all()
    if recipient_ids:
        await db.execute(delete(CampaignEvent).where(CampaignEvent.recipient_id.in_(recipient_ids)))
    await db.execute(delete(CampaignEvent).where(CampaignEvent.campaign_id == c.id))
    await db.execute(delete(CampaignRecipient).where(CampaignRecipient.campaign_id == c.id))
    await db.execute(delete(CampaignStep).where(CampaignStep.campaign_id == c.id))
    await db.delete(c)
    await db.commit()
    return {"deleted": True}


# --- steps --------------------------------------------------------------------

@router.post("/campaigns/{id}/steps")
async def add_step(id: str, body: StepCreate, ctx: Ctx = Depends(get_ctx),
                   db: AsyncSession = Depends(get_db)):
    c = await _load_campaign(db, id, ctx.workspace.id)
    count = (await db.execute(
        select(func.count()).select_from(CampaignStep).where(CampaignStep.campaign_id == c.id)
    )).scalar() or 0
    if count >= MAX_STEPS:
        raise HTTPException(status_code=400, detail="A campaign can have at most 10 steps")
    subject = (body.subject or "").strip()
    if not subject:
        raise HTTPException(status_code=400, detail="Step subject is required")
    step = CampaignStep(
        campaign_id=c.id,
        position=count + 1,
        subject=subject[:300],
        body=body.body or "",
        delay_days=body.delayDays or 0,
    )
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return _step_dict(step)


@router.delete("/campaigns/{id}/steps/{stepId}")
async def delete_step(id: str, stepId: str, ctx: Ctx = Depends(get_ctx),
                      db: AsyncSession = Depends(get_db)):
    c = await _load_campaign(db, id, ctx.workspace.id)
    step = (await db.execute(
        select(CampaignStep).where(CampaignStep.id == stepId, CampaignStep.campaign_id == c.id)
    )).scalar_one_or_none()
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    removed = step.position
    await db.delete(step)
    await db.flush()
    remaining = (await db.execute(
        select(CampaignStep).where(CampaignStep.campaign_id == c.id).order_by(CampaignStep.position)
    )).scalars().all()
    for s in remaining:
        if s.position > removed:
            s.position -= 1
    await db.commit()
    return {"deleted": True}


# --- recipients ---------------------------------------------------------------

@router.post("/campaigns/{id}/recipients")
async def add_recipients(id: str, body: RecipientsBody, ctx: Ctx = Depends(get_ctx),
                         db: AsyncSession = Depends(get_db)):
    c = await _load_campaign(db, id, ctx.workspace.id)
    ids = list(dict.fromkeys(body.contactIds or []))
    contacts = []
    if ids:
        contacts = (await db.execute(
            select(Contact).where(Contact.id.in_(ids), Contact.workspace_id == ctx.workspace.id)
        )).scalars().all()

    eligible = []
    for contact in contacts:
        email = contact.work_email or contact.direct_email
        if email:
            name = " ".join(p for p in [contact.first_name, contact.last_name] if p) or None
            eligible.append((contact, email, name))
    skipped = len(ids) - len(eligible)

    if not eligible:
        return {"added": 0, "skipped": skipped}

    workspace = (await db.execute(
        select(Workspace).where(Workspace.id == ctx.workspace.id)
    )).scalar_one()
    used = workspace.recipients_used or 0
    limit = workspace.recipients_limit if workspace.recipients_limit is not None else 0
    if used + len(eligible) > limit:
        raise HTTPException(
            status_code=409,
            detail=(f"Monthly recipient quota exceeded: {used} used of {limit}, "
                    f"adding {len(eligible)} would go over the limit"),
        )

    for contact, email, name in eligible:
        db.add(CampaignRecipient(
            campaign_id=c.id,
            contact_id=contact.id,
            email=email,
            name=name,
            status="queued",
            current_step=0,
            tracking_token=secrets.token_urlsafe(16),
        ))
    workspace.recipients_used = used + len(eligible)
    await db.commit()
    return {"added": len(eligible), "skipped": skipped}


# --- simulated send -----------------------------------------------------------

@router.post("/campaigns/{id}/send")
async def send_campaign(id: str, ctx: Ctx = Depends(get_ctx),
                        db: AsyncSession = Depends(get_db)):
    c = await _load_campaign(db, id, ctx.workspace.id)
    step_count = (await db.execute(
        select(func.count()).select_from(CampaignStep).where(CampaignStep.campaign_id == c.id)
    )).scalar() or 0
    if step_count == 0:
        raise HTTPException(status_code=400, detail="Add at least one step before sending")

    limit = c.daily_limit if c.daily_limit and c.daily_limit > 0 else 50
    recipients = (await db.execute(
        select(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == c.id,
            CampaignRecipient.status == "queued",
            CampaignRecipient.current_step == 0,
            CampaignRecipient.sent_count == 0,
        )
        .order_by(CampaignRecipient.created_at)
        .limit(limit)
    )).scalars().all()

    now = datetime.utcnow()
    for r in recipients:
        db.add(CampaignEvent(
            recipient_id=r.id,
            campaign_id=c.id,
            step_position=1,
            event_type="sent",
        ))
        r.sent_count = (r.sent_count or 0) + 1
        r.current_step = 1
        r.status = "sent"
        r.last_event_at = now
    if recipients and c.status == "draft":
        c.status = "active"
    await db.commit()
    return {"queued": len(recipients), "simulated": True}


@router.get("/campaigns/{id}/events")
async def list_events(id: str, limit: int = Query(50, ge=1, le=500),
                      ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    c = await _load_campaign(db, id, ctx.workspace.id)
    rows = (await db.execute(
        select(CampaignEvent, CampaignRecipient.email)
        .join(CampaignRecipient, CampaignRecipient.id == CampaignEvent.recipient_id)
        .where(CampaignEvent.campaign_id == c.id)
        .order_by(CampaignEvent.created_at.desc())
        .limit(limit)
    )).all()
    return {"items": [
        {
            "eventType": ev.event_type,
            "email": email,
            "stepPosition": ev.step_position,
            "createdAt": _iso(ev.created_at),
        }
        for ev, email in rows
    ]}


# --- public tracking (no auth) ------------------------------------------------

def _gif_response() -> Response:
    return Response(content=TRANSPARENT_GIF, media_type="image/gif", headers=NO_CACHE)


@public_router.get("/t/o/{token}.gif")
async def track_open(token: str, db: AsyncSession = Depends(get_db)):
    recipient = (await db.execute(
        select(CampaignRecipient).where(CampaignRecipient.tracking_token == token)
    )).scalar_one_or_none()
    if recipient:
        db.add(CampaignEvent(
            recipient_id=recipient.id,
            campaign_id=recipient.campaign_id,
            step_position=recipient.current_step or 1,
            event_type="open",
        ))
        recipient.open_count = (recipient.open_count or 0) + 1
        recipient.last_event_at = datetime.utcnow()
        await db.commit()
    return _gif_response()


@public_router.get("/t/c/{token}")
async def track_click(token: str, u: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    recipient = (await db.execute(
        select(CampaignRecipient).where(CampaignRecipient.tracking_token == token)
    )).scalar_one_or_none()
    if recipient:
        db.add(CampaignEvent(
            recipient_id=recipient.id,
            campaign_id=recipient.campaign_id,
            step_position=recipient.current_step or 1,
            event_type="click",
            meta_json='{"url": %s}' % _json_str(u),
        ))
        recipient.click_count = (recipient.click_count or 0) + 1
        recipient.last_event_at = datetime.utcnow()
        await db.commit()

    target = "/"
    if u:
        parsed = urlparse(u)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            target = u
    return RedirectResponse(url=target, status_code=302)


def _json_str(value: Optional[str]) -> str:
    import json
    return json.dumps(value)
