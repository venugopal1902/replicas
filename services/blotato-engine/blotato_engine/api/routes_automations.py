import json
import random
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db, AsyncSessionLocal
from ..models import (
    ApiKeyModel, DMAutomationModel, CommentModel, MessageModel, WebhookEventModel,
)
from ..auth import get_current_user

router = APIRouter(prefix="/v2", tags=["automations"])
webhook_router = APIRouter(tags=["webhooks"])


def _auto_dict(a: DMAutomationModel) -> Dict[str, Any]:
    return {
        "id": str(a.id),
        "accountId": a.account_id,
        "platform": a.platform,
        "name": a.name,
        "dmMessage": a.dm_message,
        "isActive": a.is_active,
        "triggers": json.loads(a.triggers_json or "[]"),
        "buttons": json.loads(a.buttons_json or "[]"),
        "commentReply": {"variants": json.loads(a.comment_reply_variants_json or "[]")},
        "followGate": json.loads(a.follow_gate_json or "{}") or None,
        "emailGate": json.loads(a.email_gate_json or "{}") or None,
        "webhook": json.loads(a.webhook_json or "{}") or None,
        "stats": {
            "triggered": a.triggered_count,
            "completed": a.completed_count,
            "failed": a.failed_count,
            "lastRunAt": a.last_run_at.isoformat() if a.last_run_at else None,
        },
        "createdAt": a.created_at.isoformat() if a.created_at else None,
    }


def _match_keywords(text: str, keywords: List[str]) -> Optional[str]:
    lowered = (text or "").lower()
    for kw in keywords or []:
        if kw and re.search(rf"\b{re.escape(kw.lower())}\b", lowered):
            return kw
    return None


async def _fire_webhook(cfg: Dict[str, Any], payload: Dict[str, Any]) -> Optional[str]:
    if not cfg or not cfg.get("url"):
        return None
    method = (cfg.get("method") or "POST").upper()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(
                method, cfg["url"], json=payload, headers=cfg.get("headers") or {}
            )
        return f"webhook {method} {cfg['url']} -> {resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        return f"webhook error: {exc}"


async def run_automations(event: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate every active automation against an inbound event and execute matches."""
    event_type = event.get("type")
    text = event.get("text", "")
    author = event.get("author", "someone")
    account_id = event.get("accountId")
    result: Dict[str, Any] = {"event": event_type, "matched": [], "skipped": 0}

    async with AsyncSessionLocal() as session:
        query = select(DMAutomationModel).where(DMAutomationModel.is_active == True)  # noqa: E712
        if account_id:
            query = query.where(DMAutomationModel.account_id == account_id)
        automations = (await session.execute(query)).scalars().all()

        for auto in automations:
            triggers = json.loads(auto.triggers_json or "[]")
            hit = None
            for trig in triggers:
                if trig.get("type") != event_type or trig.get("isActive") is False:
                    continue
                kw = _match_keywords(text, trig.get("keywords", []))
                if kw:
                    hit = kw
                    break

            if not hit:
                result["skipped"] += 1
                continue

            auto.triggered_count += 1
            auto.last_run_at = datetime.utcnow()
            record = WebhookEventModel(
                user_id=auto.user_id,
                automation_id=str(auto.id),
                event_type=event_type,
                payload_json=json.dumps(event),
                status="matched",
                detail=f"keyword={hit}",
            )
            session.add(record)
            await session.commit()

            detail_bits: List[str] = []

            variants = json.loads(auto.comment_reply_variants_json or "[]")
            if event_type == "comment-received" and variants:
                chosen = random.choice(variants)
                reply_text = chosen.get("text") if isinstance(chosen, dict) else str(chosen)
                reply_text = (reply_text or "").replace("{{mention}}", f"@{author}")
                comment_id = event.get("commentId")
                if comment_id:
                    cres = await session.execute(select(CommentModel).where(CommentModel.id == comment_id))
                    c = cres.scalar_one_or_none()
                    if c:
                        c.reply_text = reply_text
                        c.is_replied = True
                        c.replied_at = datetime.utcnow()
                detail_bits.append(f"comment reply: {reply_text[:60]}")

            dm_text = auto.dm_message
            email_gate = json.loads(auto.email_gate_json or "{}")
            if email_gate.get("message"):
                dm_text = email_gate["message"]
            follow_gate = json.loads(auto.follow_gate_json or "{}")
            if follow_gate.get("message"):
                dm_text = f"{follow_gate['message']}\n\n{dm_text}"

            thread_id = event.get("threadId") or event.get("commentId") or f"auto-{str(auto.id)[:8]}"
            session.add(MessageModel(
                user_id=auto.user_id,
                account_id=auto.account_id,
                platform=auto.platform,
                thread_id=thread_id,
                author="you",
                text=dm_text[:640],
                direction="outbound",
                is_read=True,
            ))
            detail_bits.append(f"dm sent to {author}")

            wh = json.loads(auto.webhook_json or "{}")
            if wh.get("url"):
                wh_result = await _fire_webhook(wh, {
                    "automationId": str(auto.id),
                    "keyword": hit,
                    "author": author,
                    "text": text,
                    "platform": auto.platform,
                })
                if wh_result:
                    detail_bits.append(wh_result)

            auto.completed_count += 1
            record.status = "delivered"
            record.detail = " | ".join(detail_bits)
            await session.commit()

            result["matched"].append({
                "automationId": str(auto.id),
                "name": auto.name,
                "keyword": hit,
                "actions": detail_bits,
            })

    return result


@router.get("/dm-automations")
async def list_automations(
    limit: int = Query(50, ge=1, le=250),
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(DMAutomationModel)
        .where(DMAutomationModel.user_id == user.id)
        .order_by(desc(DMAutomationModel.created_at))
        .limit(limit)
    )
    return {"items": [_auto_dict(a) for a in res.scalars().all()]}


@router.post("/dm-automations")
async def create_automation(
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    account_id = payload.get("accountId")
    dm_message = (payload.get("dmMessage") or "").strip()
    if not account_id or not dm_message:
        raise HTTPException(status_code=400, detail="accountId and dmMessage are required")

    auto = DMAutomationModel(
        user_id=user.id,
        account_id=account_id,
        platform=payload.get("platform", "instagram"),
        name=payload.get("name", "Keyword DM"),
        dm_message=dm_message,
        triggers_json=json.dumps(payload.get("triggers") or [
            {"type": "comment-received", "keywords": ["link"], "isActive": True}
        ]),
        buttons_json=json.dumps(payload.get("buttons") or []),
        comment_reply_variants_json=json.dumps((payload.get("commentReply") or {}).get("variants", [])),
        follow_gate_json=json.dumps(payload.get("followGate") or {}),
        email_gate_json=json.dumps(payload.get("emailGate") or {}),
        webhook_json=json.dumps(payload.get("webhook") or {}),
        is_active=bool(payload.get("isActive", True)),
    )
    db.add(auto)
    await db.commit()
    await db.refresh(auto)
    return _auto_dict(auto)


@router.patch("/dm-automations/{automation_id}")
async def update_automation(
    automation_id: str,
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(DMAutomationModel).where(
            DMAutomationModel.id == automation_id, DMAutomationModel.user_id == user.id
        )
    )
    auto = res.scalar_one_or_none()
    if not auto:
        raise HTTPException(status_code=404, detail="Automation not found")

    if "isActive" in payload:
        auto.is_active = bool(payload["isActive"])
    if "dmMessage" in payload:
        auto.dm_message = payload["dmMessage"]
    if "triggers" in payload:
        auto.triggers_json = json.dumps(payload["triggers"])
    if "commentReply" in payload:
        auto.comment_reply_variants_json = json.dumps((payload["commentReply"] or {}).get("variants", []))
    if "webhook" in payload:
        auto.webhook_json = json.dumps(payload["webhook"] or {})
    if "followGate" in payload:
        auto.follow_gate_json = json.dumps(payload["followGate"] or {})
    if "emailGate" in payload:
        auto.email_gate_json = json.dumps(payload["emailGate"] or {})

    await db.commit()
    await db.refresh(auto)
    return _auto_dict(auto)


@router.delete("/dm-automations/{automation_id}")
async def delete_automation(
    automation_id: str,
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(DMAutomationModel).where(
            DMAutomationModel.id == automation_id, DMAutomationModel.user_id == user.id
        )
    )
    auto = res.scalar_one_or_none()
    if not auto:
        raise HTTPException(status_code=404, detail="Automation not found")
    await db.delete(auto)
    await db.commit()
    return {"deleted": True}


@router.post("/dm-automations/{automation_id}/test")
async def test_automation(
    automation_id: str,
    payload: Dict[str, Any],
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(DMAutomationModel).where(
            DMAutomationModel.id == automation_id, DMAutomationModel.user_id == user.id
        )
    )
    auto = res.scalar_one_or_none()
    if not auto:
        raise HTTPException(status_code=404, detail="Automation not found")

    return await run_automations({
        "type": payload.get("type", "comment-received"),
        "text": payload.get("text", "link"),
        "author": payload.get("author", "test_user"),
        "accountId": auto.account_id,
        "platform": auto.platform,
    })


@router.get("/webhook-events")
async def list_events(
    limit: int = Query(50, ge=1, le=250),
    user: ApiKeyModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(WebhookEventModel)
        .where(WebhookEventModel.user_id == user.id)
        .order_by(desc(WebhookEventModel.created_at))
        .limit(limit)
    )
    return {
        "items": [
            {
                "id": str(e.id),
                "automationId": e.automation_id,
                "eventType": e.event_type,
                "status": e.status,
                "detail": e.detail,
                "createdAt": e.created_at.isoformat() if e.created_at else None,
            }
            for e in res.scalars().all()
        ]
    }


@webhook_router.post("/webhooks/meta")
async def meta_webhook(payload: Dict[str, Any]):
    """Meta callback shape for Instagram / Facebook comment and message events."""
    events: List[Dict[str, Any]] = []

    if payload.get("object") in ("instagram", "page"):
        for entry in payload.get("entry", []):
            account_id = str(entry.get("id") or "")
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if change.get("field") == "comments":
                    events.append({
                        "type": "comment-received",
                        "text": value.get("text", ""),
                        "author": (value.get("from") or {}).get("username", "someone"),
                        "accountId": account_id,
                        "platform": "instagram" if payload.get("object") == "instagram" else "facebook",
                        "commentId": value.get("id"),
                        "postId": (value.get("media") or {}).get("id"),
                    })
            for messaging in entry.get("messaging", []):
                msg = messaging.get("message", {})
                events.append({
                    "type": "message-received",
                    "text": msg.get("text", ""),
                    "author": (messaging.get("sender") or {}).get("id", "someone"),
                    "accountId": account_id,
                    "platform": "instagram",
                    "threadId": f"meta-{(messaging.get('sender') or {}).get('id', 'unknown')}",
                })

    results = [await run_automations(e) for e in events]
    return {"received": len(events), "results": results}


@webhook_router.post("/webhooks/test")
async def generic_webhook(payload: Dict[str, Any]):
    """Generic ingress for n8n / Make / custom triggers."""
    return await run_automations(payload)
