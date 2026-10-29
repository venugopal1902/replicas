import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from ..database import get_db
from ..models import ApiKeyModel, SocialAccountModel, PostModel
from ..auth import get_current_user
from ..connectors import get_connector, is_simulated

router = APIRouter(prefix="/v2", tags=["publishing"])

@router.post("/posts")
async def create_post(
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    post_data = payload.get("post", {})
    account_id = post_data.get("accountId")
    content = post_data.get("content", {})
    text = content.get("text", "")
    media_urls = content.get("mediaUrls", [])
    target = post_data.get("target", {})
    scheduled_time_str = payload.get("scheduledTime")

    if not account_id:
        raise HTTPException(status_code=400, detail="Missing required field: post.accountId")
    if not text and not media_urls:
        raise HTTPException(status_code=400, detail="Post must contain text or at least one mediaUrl")

    # Verify account ownership
    res = await db.execute(
        select(SocialAccountModel).where(
            SocialAccountModel.id == account_id,
            SocialAccountModel.user_id == user.id
        )
    )
    account = res.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Social account not found or not owned by user")

    scheduled_dt = None
    if scheduled_time_str:
        try:
            # normalize ISO string
            scheduled_dt = datetime.fromisoformat(scheduled_time_str.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid ISO 8601 format for scheduledTime")

    # Create Post Record
    post = PostModel(
        user_id=user.id,
        account_id=account.id,
        platform=account.platform,
        text=text,
        media_urls_json=json.dumps(media_urls),
        status="scheduled" if scheduled_dt else "in-progress",
        scheduled_time=scheduled_dt,
        target_meta_json=json.dumps(target)
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)

    # If immediate execution:
    simulated = False
    if not scheduled_dt:
        connector = get_connector(account.platform, account.credentials_json)
        simulated = is_simulated(connector)
        result = await connector.publish(text=text, media_urls=media_urls, target_meta=target)
        
        if result.success:
            post.status = "published"
            post.published_at = datetime.utcnow()
            post.platform_post_id = result.post_id
            post.platform_post_url = result.url
            post.simulated = simulated
            # Deduct credit if applicable
            if user.plan != "admin" and user.credits > 0:
                user.credits -= 1
        else:
            post.status = "failed"
            post.error_message = result.error

        await db.commit()
        await db.refresh(post)

    return {
        "postSubmissionId": str(post.id),
        "status": post.status,
        "platform": post.platform,
        "accountId": str(post.account_id),
        "scheduledTime": post.scheduled_time.isoformat() if post.scheduled_time else None,
        "publishedAt": post.published_at.isoformat() if post.published_at else None,
        "url": post.platform_post_url,
        "errorMessage": post.error_message,
        "simulated": bool(post.simulated)
    }

@router.get("/posts/{postSubmissionId}")
async def get_post(
    postSubmissionId: str,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(
        select(PostModel).where(
            PostModel.id == postSubmissionId,
            PostModel.user_id == user.id
        )
    )
    post = res.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post submission not found")

    return {
        "postSubmissionId": str(post.id),
        "status": post.status,
        "platform": post.platform,
        "accountId": str(post.account_id),
        "text": post.text,
        "mediaUrls": json.loads(post.media_urls_json or "[]"),
        "scheduledTime": post.scheduled_time.isoformat() if post.scheduled_time else None,
        "publishedAt": post.published_at.isoformat() if post.published_at else None,
        "url": post.platform_post_url,
        "errorMessage": post.error_message,
        "simulated": bool(post.simulated)
    }

@router.get("/posts")
async def list_posts(
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    query = select(PostModel).where(PostModel.user_id == user.id)
    if status:
        query = query.where(PostModel.status == status)
    query = query.order_by(desc(PostModel.created_at)).limit(limit)

    res = await db.execute(query)
    posts = res.scalars().all()

    return {
        "items": [
            {
                "postSubmissionId": str(p.id),
                "status": p.status,
                "platform": p.platform,
                "accountId": str(p.account_id),
                "text": p.text,
                "mediaUrls": json.loads(p.media_urls_json or "[]"),
                "scheduledTime": p.scheduled_time.isoformat() if p.scheduled_time else None,
                "publishedAt": p.published_at.isoformat() if p.published_at else None,
                "url": p.platform_post_url,
                "simulated": bool(p.simulated),
                "createdAt": p.created_at.isoformat() if p.created_at else None
            }
            for p in posts
        ]
    }
