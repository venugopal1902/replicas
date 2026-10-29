from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..database import get_db
from ..models import Mailbox
from ..serializers import mailbox_dict, workspace_dict
from .auth_routes import workspace_stats

router = APIRouter(prefix="/api", tags=["workspace"])


class WorkspacePatch(BaseModel):
    name: str | None = None
    dailyLimit: int | None = None
    sendingDomain: str | None = None
    warmupEnabled: bool | None = None
    timezone: str | None = None


class MailboxBody(BaseModel):
    email: str
    displayName: str | None = None
    provider: str | None = "simulated"
    dailyLimit: int | None = 50


class MailboxPatch(BaseModel):
    status: str | None = None
    dailyLimit: int | None = None
    displayName: str | None = None
    provider: str | None = None


@router.get("/workspace")
async def get_workspace(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    return {"workspace": workspace_dict(ctx.workspace, await workspace_stats(ctx.workspace.id, db))}


@router.patch("/workspace")
async def patch_workspace(body: WorkspacePatch, ctx: Ctx = Depends(get_ctx),
                          db: AsyncSession = Depends(get_db)):
    ws = ctx.workspace
    if body.name is not None:
        ws.name = body.name.strip() or ws.name
    if body.dailyLimit is not None:
        if body.dailyLimit < 1 or body.dailyLimit > 5000:
            raise HTTPException(status_code=400, detail="dailyLimit must be between 1 and 5000")
        ws.daily_limit = body.dailyLimit
    if body.sendingDomain is not None:
        ws.sending_domain = body.sendingDomain.strip()
    if body.warmupEnabled is not None:
        ws.warmup_enabled = body.warmupEnabled
    if body.timezone is not None:
        ws.timezone = body.timezone
    await db.commit()
    await db.refresh(ws)
    return {"workspace": workspace_dict(ws, await workspace_stats(ws.id, db))}


@router.get("/mailboxes")
async def list_mailboxes(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Mailbox).where(Mailbox.workspace_id == ctx.workspace.id).order_by(Mailbox.created_at)
    )).scalars().all()
    items = [mailbox_dict(m) for m in rows]
    return {"items": items, "total": len(items)}


@router.post("/mailboxes", status_code=201)
async def create_mailbox(body: MailboxBody, ctx: Ctx = Depends(get_ctx),
                         db: AsyncSession = Depends(get_db)):
    email = body.email.lower().strip()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email address is required")
    existing = (await db.execute(
        select(Mailbox).where(Mailbox.workspace_id == ctx.workspace.id, Mailbox.email == email)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Mailbox already exists in this workspace")
    mb = Mailbox(
        workspace_id=ctx.workspace.id,
        email=email,
        display_name=body.displayName or email.split("@")[0].title(),
        provider=body.provider or "simulated",
        daily_limit=max(1, body.dailyLimit or 50),
        warmup_score=0.0,
    )
    db.add(mb)
    await db.commit()
    await db.refresh(mb)
    return {"mailbox": mailbox_dict(mb)}


@router.patch("/mailboxes/{mailbox_id}")
async def patch_mailbox(mailbox_id: str, body: MailboxPatch, ctx: Ctx = Depends(get_ctx),
                        db: AsyncSession = Depends(get_db)):
    mb = (await db.execute(select(Mailbox).where(
        Mailbox.id == mailbox_id, Mailbox.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if not mb:
        raise HTTPException(status_code=404, detail="Mailbox not found")
    if body.status is not None:
        if body.status not in ("active", "paused", "error"):
            raise HTTPException(status_code=400, detail="status must be active, paused or error")
        mb.status = body.status
    if body.dailyLimit is not None:
        mb.daily_limit = max(1, body.dailyLimit)
    if body.displayName is not None:
        mb.display_name = body.displayName
    if body.provider is not None:
        mb.provider = body.provider
    await db.commit()
    await db.refresh(mb)
    return {"mailbox": mailbox_dict(mb)}


@router.delete("/mailboxes/{mailbox_id}")
async def delete_mailbox(mailbox_id: str, ctx: Ctx = Depends(get_ctx),
                         db: AsyncSession = Depends(get_db)):
    mb = (await db.execute(select(Mailbox).where(
        Mailbox.id == mailbox_id, Mailbox.workspace_id == ctx.workspace.id))).scalar_one_or_none()
    if not mb:
        raise HTTPException(status_code=404, detail="Mailbox not found")
    await db.delete(mb)
    await db.commit()
    return {"ok": True, "deleted": mailbox_id}
