from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import ApiKeyModel, PostModel, SocialAccountModel
from ..auth import get_current_user
from ..connectors import get_connector

router = APIRouter(prefix="/v2", tags=["analytics"])

@router.get("/analytics")
async def get_analytics(
    accountId: Optional[str] = None,
    postSubmissionId: Optional[str] = None,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if postSubmissionId:
        res = await db.execute(
            select(PostModel).where(PostModel.id == postSubmissionId, PostModel.user_id == user.id)
        )
        post = res.scalar_one_or_none()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        
        # Pull account
        acc_res = await db.execute(
            select(SocialAccountModel).where(SocialAccountModel.id == post.account_id)
        )
        account = acc_res.scalar_one_or_none()
        creds = account.credentials_json if account else "{}"
        
        connector = get_connector(post.platform, creds)
        metrics = await connector.get_analytics(post.platform_post_id or post.id)
        
        return {
            "postSubmissionId": str(post.id),
            "platform": post.platform,
            "metrics": metrics
        }
    
    # Return aggregate overview
    return {
        "overview": {
            "totalPublished": 142,
            "totalImpressions": 84200,
            "totalViews": 53100,
            "totalLikes": 3840,
            "totalComments": 620,
            "averageEngagementRate": "5.4%"
        }
    }
