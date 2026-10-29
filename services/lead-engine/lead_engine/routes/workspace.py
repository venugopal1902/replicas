import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..database import get_db
from ..models import ApiKey, CreditEvent, WorkspaceMember
from .auth_routes import workspace_dict

router = APIRouter(prefix="/api", tags=["workspace"])


class KeyBody(BaseModel):
    label: str | None = "default"


@router.get("/workspace")
async def get_workspace(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    seats = (await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == ctx.workspace.id)
    )).scalars().all()
    return workspace_dict(ctx.workspace, max(len(seats), 1))


@router.get("/credits/ledger")
async def ledger(limit: int = Query(50, ge=1, le=250), ctx: Ctx = Depends(get_ctx),
                 db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(CreditEvent).where(CreditEvent.workspace_id == ctx.workspace.id)
        .order_by(desc(CreditEvent.created_at)).limit(limit)
    )).scalars().all()
    return {
        "items": [
            {
                "id": e.id,
                "delta": e.delta,
                "pool": e.pool,
                "reason": e.reason,
                "contactId": e.contact_id,
                "balanceAfter": e.balance_after,
                "createdAt": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ]
    }


@router.get("/api-keys")
async def list_keys(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(ApiKey).where(ApiKey.workspace_id == ctx.workspace.id).order_by(ApiKey.created_at)
    )).scalars().all()
    return {
        "items": [
            {
                "id": k.id, "label": k.label, "key": k.key,
                "callsToday": k.calls_today or 0, "callsLimit": k.calls_limit,
                "createdAt": k.created_at.isoformat() if k.created_at else None,
            }
            for k in rows
        ]
    }


@router.post("/api-keys")
async def create_key(body: KeyBody, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    key = ApiKey(
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
        key=f"lke_live_{secrets.token_urlsafe(24)}",
        label=(body.label or "default")[:120],
        calls_limit=ctx.workspace.api_calls_limit,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return {"id": key.id, "label": key.label, "key": key.key,
            "callsToday": 0, "callsLimit": key.calls_limit,
            "createdAt": key.created_at.isoformat() if key.created_at else None}


@router.delete("/api-keys/{key_id}")
async def delete_key(key_id: str, ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    k = (await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.workspace_id == ctx.workspace.id)
    )).scalar_one_or_none()
    if not k:
        raise HTTPException(status_code=404, detail="Key not found")
    await db.delete(k)
    await db.commit()
    return {"deleted": True}
