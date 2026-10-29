import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import (
    ApiKeyModel, PostModel, SocialAccountModel, AnalyticsSnapshotModel,
)
from ..auth import get_current_user
from ..connectors import get_connector

router = APIRouter(prefix="/v2", tags=["analytics"])

METRIC_FIELDS = ("views", "likes", "comments", "shares", "saves", "reach")


def _snapshot_dict(s: AnalyticsSnapshotModel) -> Dict[str, Any]:
    return {
        "views": s.views,
        "likes": s.likes,
        "comments": s.comments,
        "shares": s.shares,
        "saves": s.saves,
        "reach": s.reach,
        "capturedAt": s.captured_at.isoformat() if s.captured_at else None,
    }


@router.get("/analytics/top")
async def top_posts(
    metric: str = Query("views"),
    limit: int = Query(10, ge=1, le=50),
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if metric not in METRIC_FIELDS:
        raise HTTPException(status_code=400, detail=f"metric must be one of {', '.join(METRIC_FIELDS)}")

    col = getattr(AnalyticsSnapshotModel, metric)
    res = await db.execute(
        select(AnalyticsSnapshotModel, PostModel)
        .join(PostModel, PostModel.id == AnalyticsSnapshotModel.post_submission_id)
        .where(AnalyticsSnapshotModel.user_id == user.id)
        .order_by(desc(col))
        .limit(limit)
    )

    items = []
    for snap, post in res.all():
        items.append({
            "postSubmissionId": str(post.id),
            "platform": post.platform,
            "text": post.text,
            "url": post.platform_post_url,
            "publishedAt": post.published_at.isoformat() if post.published_at else None,
            "views": snap.views,
            "likes": snap.likes,
            "comments": snap.comments,
            "shares": snap.shares,
            "saves": snap.saves,
            "reach": snap.reach,
        })
    return {"metric": metric, "items": items}


@router.get("/analytics")
async def get_analytics(
    accountId: Optional[str] = None,
    postSubmissionId: Optional[str] = None,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if postSubmissionId:
        res = await db.execute(
            select(PostModel).where(PostModel.id == postSubmissionId, PostModel.user_id == user.id)
        )
        post = res.scalar_one_or_none()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")

        acc_res = await db.execute(
            select(SocialAccountModel).where(SocialAccountModel.id == post.account_id)
        )
        account = acc_res.scalar_one_or_none()
        creds = account.credentials_json if account else "{}"

        connector = get_connector(post.platform, creds)
        live_metrics = await connector.get_analytics(post.platform_post_id or post.id)

        snap_res = await db.execute(
            select(AnalyticsSnapshotModel)
            .where(AnalyticsSnapshotModel.post_submission_id == post.id)
            .order_by(AnalyticsSnapshotModel.captured_at)
        )
        history = [_snapshot_dict(s) for s in snap_res.scalars().all()]

        metrics = live_metrics or {}
        if history:
            latest = history[-1]
            metrics = {
                "views": latest["views"],
                "likes": latest["likes"],
                "comments": latest["comments"],
                "shares": latest["shares"],
                "saves": latest["saves"],
                "reach": latest["reach"],
            }

        return {
            "postSubmissionId": str(post.id),
            "platform": post.platform,
            "text": post.text,
            "url": post.platform_post_url,
            "metrics": metrics,
            "history": history,
        }

    totals = {field: 0 for field in METRIC_FIELDS}
    query = select(
        func.sum(AnalyticsSnapshotModel.views),
        func.sum(AnalyticsSnapshotModel.likes),
        func.sum(AnalyticsSnapshotModel.comments),
        func.sum(AnalyticsSnapshotModel.shares),
        func.sum(AnalyticsSnapshotModel.saves),
        func.sum(AnalyticsSnapshotModel.reach),
    ).where(AnalyticsSnapshotModel.user_id == user.id)
    row = (await db.execute(query)).one()
    for i, field in enumerate(METRIC_FIELDS):
        totals[field] = row[i] or 0

    published = (await db.execute(
        select(func.count()).select_from(PostModel).where(
            PostModel.user_id == user.id, PostModel.status == "published"
        )
    )).scalar() or 0
    scheduled = (await db.execute(
        select(func.count()).select_from(PostModel).where(
            PostModel.user_id == user.id, PostModel.status == "scheduled"
        )
    )).scalar() or 0
    failed = (await db.execute(
        select(func.count()).select_from(PostModel).where(
            PostModel.user_id == user.id, PostModel.status == "failed"
        )
    )).scalar() or 0
    accounts = (await db.execute(
        select(func.count()).select_from(SocialAccountModel).where(
            SocialAccountModel.user_id == user.id
        )
    )).scalar() or 0

    engagement = totals["likes"] + totals["comments"] + totals["shares"]
    rate = (engagement / totals["reach"] * 100) if totals["reach"] else 0.0

    by_platform_res = await db.execute(
        select(
            AnalyticsSnapshotModel.platform,
            func.sum(AnalyticsSnapshotModel.views),
            func.sum(AnalyticsSnapshotModel.likes),
        )
        .where(AnalyticsSnapshotModel.user_id == user.id)
        .group_by(AnalyticsSnapshotModel.platform)
    )
    by_platform = [
        {"platform": p, "views": int(v or 0), "likes": int(l or 0)}
        for p, v, l in by_platform_res.all()
    ]

    return {
        "overview": {
            "totalPublished": int(published),
            "totalScheduled": int(scheduled),
            "totalFailed": int(failed),
            "connectedAccounts": int(accounts),
            "totalImpressions": totals["views"],
            "totalViews": totals["views"],
            "totalLikes": totals["likes"],
            "totalComments": totals["comments"],
            "totalShares": totals["shares"],
            "totalSaves": totals["saves"],
            "totalReach": totals["reach"],
            "averageEngagementRate": f"{rate:.1f}%",
        },
        "byPlatform": by_platform,
    }


@router.post("/analytics/snapshot")
async def capture_snapshot(
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Capture the current metrics for a post. Called on a schedule or manually."""
    post_id = payload.get("postSubmissionId")
    res = await db.execute(
        select(PostModel).where(PostModel.id == post_id, PostModel.user_id == user.id)
    )
    post = res.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    snap = AnalyticsSnapshotModel(
        user_id=user.id,
        post_submission_id=post.id,
        platform=post.platform,
        views=int(payload.get("views", 0)),
        likes=int(payload.get("likes", 0)),
        comments=int(payload.get("comments", 0)),
        shares=int(payload.get("shares", 0)),
        saves=int(payload.get("saves", 0)),
        reach=int(payload.get("reach", 0)),
    )
    db.add(snap)
    await db.commit()
    await db.refresh(snap)
    return _snapshot_dict(snap)
