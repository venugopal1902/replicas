import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import ApiKeyModel, CommentModel, MessageModel, SocialAccountModel
from ..auth import get_current_user

comments_router = APIRouter(prefix="/v2/comments", tags=["comments"])
messages_router = APIRouter(prefix="/v2/messages", tags=["messaging"])


def _comment_dict(c: CommentModel) -> Dict[str, Any]:
    return {
        "id": str(c.id),
        "accountId": c.account_id,
        "platform": c.platform,
        "postId": c.platform_post_id,
        "author": c.author,
        "text": c.text,
        "isReplied": c.is_replied,
        "replyText": c.reply_text,
        "createdAt": c.created_at.isoformat() if c.created_at else None,
    }


def _message_dict(m: MessageModel) -> Dict[str, Any]:
    return {
        "id": str(m.id),
        "accountId": m.account_id,
        "platform": m.platform,
        "threadId": m.thread_id,
        "author": m.author,
        "text": m.text,
        "direction": m.direction,
        "isRead": m.is_read,
        "createdAt": m.created_at.isoformat() if m.created_at else None,
    }


# ------------------------------------------------------------------ comments

@comments_router.get("")
async def list_comments(
    accountId: Optional[str] = None,
    unreadOnly: bool = False,
    limit: int = Query(50, ge=1, le=250),
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(CommentModel).where(CommentModel.user_id == user.id)
    if accountId:
        query = query.where(CommentModel.account_id == accountId)
    if unreadOnly:
        query = query.where(CommentModel.is_replied == False)  # noqa: E712
    query = query.order_by(desc(CommentModel.created_at)).limit(limit)
    res = await db.execute(query)
    return {"items": [_comment_dict(c) for c in res.scalars().all()]}


@comments_router.post("/{comment_id}/reply")
async def reply_to_comment(
    comment_id: str,
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    res = await db.execute(
        select(CommentModel).where(CommentModel.id == comment_id, CommentModel.user_id == user.id)
    )
    comment = res.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Platform reply would go here; mock keeps the flow complete without credentials
    comment.reply_text = text
    comment.is_replied = True
    comment.replied_at = datetime.utcnow()
    await db.commit()
    await db.refresh(comment)
    return _comment_dict(comment)


@comments_router.post("/{comment_id}/read")
async def mark_comment_read(
    comment_id: str,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(CommentModel).where(CommentModel.id == comment_id, CommentModel.user_id == user.id)
    )
    comment = res.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    comment.is_replied = True
    await db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ messages

@messages_router.get("")
async def list_threads(
    accountId: Optional[str] = None,
    limit: int = Query(50, ge=1, le=250),
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(MessageModel).where(MessageModel.user_id == user.id)
    if accountId:
        query = query.where(MessageModel.account_id == accountId)
    query = query.order_by(desc(MessageModel.created_at))
    res = await db.execute(query)
    rows = res.scalars().all()

    threads: Dict[str, Dict[str, Any]] = {}
    for m in rows:
        key = m.thread_id
        if key not in threads:
            threads[key] = {
                "threadId": key,
                "accountId": m.account_id,
                "platform": m.platform,
                "participant": m.author if m.direction == "inbound" else "you",
                "lastMessage": m.text,
                "lastAt": m.created_at.isoformat() if m.created_at else None,
                "unread": 0,
            }
        if m.direction == "inbound" and not m.is_read:
            threads[key]["unread"] += 1

    items = list(threads.values())[:limit]
    return {"items": items, "total": len(threads)}


@messages_router.get("/{thread_id}")
async def get_thread(
    thread_id: str,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(MessageModel)
        .where(MessageModel.user_id == user.id, MessageModel.thread_id == thread_id)
        .order_by(MessageModel.created_at)
    )
    msgs = res.scalars().all()
    if not msgs:
        raise HTTPException(status_code=404, detail="Thread not found")

    for m in msgs:
        m.is_read = True
    await db.commit()

    return {"threadId": thread_id, "messages": [_message_dict(m) for m in msgs]}


@messages_router.post("")
async def send_message(
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    thread_id = payload.get("threadId")
    text = (payload.get("text") or "").strip()
    account_id = payload.get("accountId")

    if not thread_id or not text:
        raise HTTPException(status_code=400, detail="threadId and text are required")

    if not account_id:
        existing = await db.execute(
            select(MessageModel).where(
                MessageModel.user_id == user.id, MessageModel.thread_id == thread_id
            ).limit(1)
        )
        found = existing.scalar_one_or_none()
        account_id = found.account_id if found else None
        platform = found.platform if found else "instagram"
    else:
        acc_res = await db.execute(
            select(SocialAccountModel).where(
                SocialAccountModel.id == account_id, SocialAccountModel.user_id == user.id
            )
        )
        acc = acc_res.scalar_one_or_none()
        platform = acc.platform if acc else "instagram"

    msg = MessageModel(
        user_id=user.id,
        account_id=account_id,
        platform=platform,
        thread_id=thread_id,
        author="you",
        text=text,
        direction="outbound",
        is_read=True,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return _message_dict(msg)
