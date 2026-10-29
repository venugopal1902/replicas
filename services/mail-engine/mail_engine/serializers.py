"""JSON serialisers shared by every route. camelCase on the wire, snake_case in the ORM."""
from datetime import date, datetime
from typing import Optional

from .models import (
    Campaign, Lead, Mailbox, Send, SequenceStep, InboxMessage, Suppression, Workspace,
)


def iso(value: Optional[datetime | date]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return value.isoformat()


def pct(part: int, whole: int) -> float:
    if not whole:
        return 0.0
    return round(100.0 * part / whole, 1)


def workspace_dict(ws: Workspace, stats: Optional[dict] = None) -> dict:
    return {
        "id": ws.id,
        "name": ws.name,
        "plan": ws.plan,
        "dailyLimit": ws.daily_limit,
        "sendingDomain": ws.sending_domain,
        "warmupEnabled": bool(ws.warmup_enabled),
        "timezone": ws.timezone,
        "stats": stats or {},
        "createdAt": iso(ws.created_at),
    }


def mailbox_dict(mb: Mailbox) -> dict:
    used = mb.sent_today or 0
    limit = mb.daily_limit or 0
    return {
        "id": mb.id,
        "email": mb.email,
        "displayName": mb.display_name,
        "provider": mb.provider,
        "status": mb.status,
        "dailyLimit": limit,
        "sentToday": used,
        "remainingToday": max(limit - used, 0),
        "warmupScore": round(mb.warmup_score or 0.0, 1),
        "warmupEnabled": bool(mb.warmup_enabled),
        "lastReset": iso(mb.last_reset),
    }


def campaign_dict(c: Campaign, stats: Optional[dict] = None) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "status": c.status,
        "subject": c.subject or "",
        "body": c.body or "",
        "dailyLimit": c.daily_limit,
        "stopOnReply": bool(c.stop_on_reply),
        "fromMailboxId": c.from_mailbox_id,
        "trackingEnabled": bool(c.tracking_enabled),
        "createdAt": iso(c.created_at),
        "activatedAt": iso(c.activated_at),
        "stats": stats or {},
    }


def step_dict(s: SequenceStep) -> dict:
    return {
        "id": s.id,
        "campaignId": s.campaign_id,
        "stepOrder": s.step_order,
        "delayDays": s.delay_days,
        "subject": s.subject or "",
        "body": s.body or "",
    }


def lead_dict(l: Lead) -> dict:
    return {
        "id": l.id,
        "email": l.email,
        "firstName": l.first_name,
        "lastName": l.last_name,
        "company": l.company,
        "title": l.title,
        "domain": l.domain,
        "status": l.status,
        "source": l.source,
        "createdAt": iso(l.created_at),
    }


def send_dict(s: Send) -> dict:
    return {
        "id": s.id,
        "campaignId": s.campaign_id,
        "leadId": s.lead_id,
        "mailboxId": s.mailbox_id,
        "stepOrder": s.step_order,
        "to": s.to_email,
        "subject": s.subject,
        "status": s.status,
        "blockedReason": s.blocked_reason,
        "trackingId": s.tracking_id,
        "direction": s.direction,
        "sentAt": iso(s.sent_at),
        "openedAt": iso(s.opened_at),
        "clickedAt": iso(s.clicked_at),
        "repliedAt": iso(s.replied_at),
        "bouncedAt": iso(s.bounced_at),
    }


def message_dict(m: InboxMessage) -> dict:
    return {
        "id": m.id,
        "fromEmail": m.from_email,
        "fromName": m.from_name,
        "subject": m.subject,
        "body": m.body,
        "classification": m.classification,
        "isRead": bool(m.is_read),
        "receivedAt": iso(m.received_at),
        "campaignId": m.campaign_id,
        "leadId": m.lead_id,
        "mailboxId": m.mailbox_id,
    }


def suppression_dict(s: Suppression) -> dict:
    return {
        "id": s.id,
        "email": s.email,
        "reason": s.reason,
        "createdAt": iso(s.created_at),
    }
