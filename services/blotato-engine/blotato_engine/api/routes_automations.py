import json
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from ..database import get_db
from ..models import ApiKeyModel, DMAutomationModel
from ..auth import get_current_user

router = APIRouter(prefix="/v2", tags=["automations"])

@router.get("/dm-automations")
async def list_automations(
    limit: int = Query(50, ge=1, le=250),
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(
        select(DMAutomationModel)
        .where(DMAutomationModel.user_id == user.id)
        .order_by(desc(DMAutomationModel.created_at))
        .limit(limit)
    )
    items = res.scalars().all()
    
    return {
        "items": [
            {
                "id": str(a.id),
                "accountId": a.account_id,
                "platform": a.platform,
                "name": a.name,
                "dmMessage": a.dm_message,
                "isActive": a.is_active,
                "triggers": json.loads(a.triggers_json or "[]"),
                "buttons": json.loads(a.buttons_json or "[]"),
                "stats": {
                    "triggered": a.triggered_count,
                    "completed": a.completed_count,
                    "failed": a.failed_count,
                    "lastRunAt": None
                },
                "createdAt": a.created_at.isoformat() if a.created_at else None
            }
            for a in items
        ]
    }

@router.post("/dm-automations")
async def create_automation(
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    account_id = payload.get("accountId")
    platform = payload.get("platform", "instagram")
    name = payload.get("name", "Keyword DM Lead Magnet")
    dm_message = payload.get("dmMessage", "")
    triggers = payload.get("triggers", [])
    buttons = payload.get("buttons", [])
    comment_reply = payload.get("commentReply", {})

    if not account_id or not dm_message:
        raise HTTPException(status_code=400, detail="Missing accountId or dmMessage")

    automation = DMAutomationModel(
        user_id=user.id,
        account_id=account_id,
        platform=platform,
        name=name,
        dm_message=dm_message,
        triggers_json=json.dumps(triggers),
        buttons_json=json.dumps(buttons),
        comment_reply_variants_json=json.dumps(comment_reply.get("variants", []))
    )
    db.add(automation)
    await db.commit()
    await db.refresh(automation)

    return {
        "id": str(automation.id),
        "name": automation.name,
        "platform": automation.platform,
        "isActive": automation.is_active,
        "dmMessage": automation.dm_message
    }
