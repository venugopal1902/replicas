import uuid
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import ApiKeyModel
from ..auth import get_current_user

router = APIRouter(prefix="/v2", tags=["billing"])

@router.get("/credits")
async def get_credits(user: ApiKeyModel = Depends(get_current_user)):
    return {
        "balance": user.credits if user.credits != -1 else 999999,
        "isUnlimited": user.plan == "admin" or user.credits == -1,
        "plan": user.plan,
        "rateLimitPerHour": user.rate_limit_per_hour,
        "clientName": user.client_name
    }

# Admin endpoint to issue keys to clients/customers
@router.post("/admin/keys")
async def issue_client_api_key(
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if user.plan != "admin":
        raise HTTPException(status_code=403, detail="Only admin can issue new API keys")

    client_name = payload.get("clientName", "New Client")
    plan = payload.get("plan", "starter")
    credits = payload.get("credits", 1000)
    
    new_token = f"blt_{plan}_{uuid.uuid4().hex[:20]}"
    
    new_key = ApiKeyModel(
        api_key=new_token,
        client_name=client_name,
        plan=plan,
        credits=credits,
        rate_limit_per_hour=1800 if plan == "starter" else 3600,
        is_active=True
    )
    db.add(new_key)
    await db.commit()
    await db.refresh(new_key)

    return {
        "apiKey": new_token,
        "clientName": new_key.client_name,
        "plan": new_key.plan,
        "credits": new_key.credits,
        "rateLimitPerHour": new_key.rate_limit_per_hour
    }
