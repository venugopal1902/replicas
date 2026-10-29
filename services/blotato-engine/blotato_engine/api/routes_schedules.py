import json
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import ApiKeyModel, PostModel, ScheduleSlotModel
from ..auth import get_current_user

router = APIRouter(prefix="/v2", tags=["scheduling"])

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _post_dict(p: PostModel) -> Dict[str, Any]:
    return {
        "id": str(p.id),
        "postSubmissionId": str(p.id),
        "accountId": str(p.account_id),
        "platform": p.platform,
        "text": p.text,
        "mediaUrls": json.loads(p.media_urls_json or "[]"),
        "status": p.status,
        "scheduledTime": p.scheduled_time.isoformat() if p.scheduled_time else None,
        "publishedAt": p.published_at.isoformat() if p.published_at else None,
        "url": p.platform_post_url,
    }


def _slot_dict(s: ScheduleSlotModel) -> Dict[str, Any]:
    return {
        "id": str(s.id),
        "accountId": s.account_id,
        "platform": s.platform,
        "dayOfWeek": s.day_of_week,
        "dayName": DAY_NAMES[s.day_of_week % 7],
        "hour": s.hour,
        "minute": s.minute,
        "time": f"{s.hour:02d}:{s.minute:02d}",
        "timezone": s.timezone,
        "isActive": s.is_active,
    }


def next_slot_time(slots, after: Optional[datetime] = None, tz_name: str = "Asia/Kolkata") -> Optional[datetime]:
    """Find the earliest weekly slot strictly after `after`, evaluated in the slot timezone."""
    tz = ZoneInfo(tz_name)
    now = after or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)

    best = None
    for s in slots:
        if not s.is_active:
            continue
        for add_days in range(0, 15):
            day = now + timedelta(days=add_days)
            candidate = day.replace(hour=s.hour, minute=s.minute, second=0, microsecond=0)
            if candidate.weekday() == (s.day_of_week % 7) and candidate > now:
                if best is None or candidate < best:
                    best = candidate
                break
    return best


@router.get("/schedules")
async def list_schedules(
    limit: int = Query(50, ge=1, le=250),
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(PostModel)
        .where(PostModel.user_id == user.id, PostModel.status == "scheduled")
        .order_by(PostModel.scheduled_time)
        .limit(limit)
    )
    return {"items": [_post_dict(p) for p in res.scalars().all()]}


@router.patch("/schedules/{post_id}")
async def update_schedule(
    post_id: str,
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(PostModel).where(PostModel.id == post_id, PostModel.user_id == user.id)
    )
    post = res.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Scheduled post not found")

    patch = payload.get("patch", payload)
    new_time = patch.get("scheduledTime") or patch.get("scheduled_time")
    if new_time:
        try:
            post.scheduled_time = datetime.fromisoformat(str(new_time).replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid ISO 8601 scheduledTime")
    if "text" in patch:
        post.text = patch["text"]
    if patch.get("cancel"):
        post.status = "draft"
    await db.commit()
    await db.refresh(post)
    return _post_dict(post)


@router.delete("/schedules/{post_id}")
async def cancel_schedule(
    post_id: str,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(PostModel).where(PostModel.id == post_id, PostModel.user_id == user.id)
    )
    post = res.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Scheduled post not found")
    post.status = "draft"
    post.scheduled_time = None
    await db.commit()
    return {"cancelled": True, "postSubmissionId": str(post.id)}


@router.get("/schedule-slots")
async def list_slots(
    accountId: Optional[str] = None,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(ScheduleSlotModel).where(ScheduleSlotModel.user_id == user.id)
    if accountId:
        query = query.where(ScheduleSlotModel.account_id == accountId)
    query = query.order_by(ScheduleSlotModel.day_of_week, ScheduleSlotModel.hour)
    res = await db.execute(query)
    slots = res.scalars().all()
    return {"items": [_slot_dict(s) for s in slots]}


@router.post("/schedule-slots")
async def create_slot(
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    account_id = payload.get("accountId")
    if not account_id:
        raise HTTPException(status_code=400, detail="accountId is required")

    slot = ScheduleSlotModel(
        user_id=user.id,
        account_id=account_id,
        platform=payload.get("platform", "instagram"),
        day_of_week=int(payload.get("dayOfWeek", 1)) % 7,
        hour=int(payload.get("hour", 9)) % 24,
        minute=int(payload.get("minute", 0)) % 60,
        timezone=payload.get("timezone", "Asia/Kolkata"),
        is_active=bool(payload.get("isActive", True)),
    )
    db.add(slot)
    await db.commit()
    await db.refresh(slot)
    return _slot_dict(slot)


@router.delete("/schedule-slots/{slot_id}")
async def delete_slot(
    slot_id: str,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(ScheduleSlotModel).where(
            ScheduleSlotModel.id == slot_id, ScheduleSlotModel.user_id == user.id
        )
    )
    slot = res.scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    await db.delete(slot)
    await db.commit()
    return {"deleted": True}


@router.get("/schedule-slots/next")
async def next_free_slot(
    accountId: Optional[str] = None,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(ScheduleSlotModel).where(
        ScheduleSlotModel.user_id == user.id, ScheduleSlotModel.is_active == True  # noqa: E712
    )
    if accountId:
        query = query.where(ScheduleSlotModel.account_id == accountId)
    res = await db.execute(query)
    slots = res.scalars().all()
    if not slots:
        raise HTTPException(status_code=404, detail="No active schedule slots configured")

    upcoming = next_slot_time(slots, tz_name=slots[0].timezone)
    if not upcoming:
        raise HTTPException(status_code=404, detail="No future slot available")

    # Skip slots already occupied by a scheduled post
    taken_res = await db.execute(
        select(PostModel).where(
            PostModel.user_id == user.id,
            PostModel.status == "scheduled",
            PostModel.scheduled_time.isnot(None),
        )
    )
    taken = {
        p.scheduled_time.replace(tzinfo=p.scheduled_time.tzinfo or dt_timezone.utc)
        for p in taken_res.scalars().all()
        if p.scheduled_time
    }
    for _ in range(20):
        key = upcoming.replace(tzinfo=None)
        clash = any(t.replace(tzinfo=None) == key for t in taken)
        if not clash:
            break
        upcoming = next_slot_time(slots, after=upcoming, tz_name=slots[0].timezone)
        if not upcoming:
            break

    return {
        "slotTime": upcoming.isoformat() if upcoming else None,
        "timezone": slots[0].timezone,
    }
