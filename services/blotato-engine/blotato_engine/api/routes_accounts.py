import json
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import ApiKeyModel, SocialAccountModel
from ..auth import get_current_user

router = APIRouter(prefix="/v2", tags=["accounts"])

@router.get("/users/me/accounts")
async def list_accounts(
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(SocialAccountModel).where(SocialAccountModel.user_id == user.id)
    )
    accounts = result.scalars().all()
    
    return {
        "accounts": [
            {
                "id": str(acc.id),
                "platform": acc.platform,
                "name": acc.name,
                "username": acc.username,
                "isActive": acc.is_active,
                "createdAt": acc.created_at.isoformat() if acc.created_at else None
            }
            for acc in accounts
        ]
    }

@router.post("/users/me/accounts")
async def add_account(
    data: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    platform = data.get("platform")
    name = data.get("name", f"{platform.title()} Account")
    username = data.get("username")
    credentials = data.get("credentials", {})

    if not platform:
        raise HTTPException(status_code=400, detail="Missing required field: platform")

    account = SocialAccountModel(
        user_id=user.id,
        platform=platform.lower(),
        name=name,
        username=username,
        credentials_json=json.dumps(credentials)
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    return {
        "id": str(account.id),
        "platform": account.platform,
        "name": account.name,
        "username": account.username,
        "isActive": account.is_active
    }
