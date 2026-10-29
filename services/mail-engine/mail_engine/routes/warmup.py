from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..database import get_db
from ..models import Mailbox, WarmupDay
from ..serializers import iso

router = APIRouter(prefix="/api/warmup", tags=["warmup"])


@router.get("")
async def get_warmup(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    mailboxes = (await db.execute(select(Mailbox).where(
        Mailbox.workspace_id == ctx.workspace.id).order_by(Mailbox.created_at))).scalars().all()
    items = []
    for mb in mailboxes:
        days = (await db.execute(select(WarmupDay).where(
            WarmupDay.mailbox_id == mb.id).order_by(WarmupDay.day))).scalars().all()
        items.append({
            "mailboxId": mb.id,
            "email": mb.email,
            "status": mb.status,
            "warmupScore": round(mb.warmup_score or 0.0, 1),
            "days": [{
                "day": iso(d.day),
                "sent": d.sent,
                "received": d.received,
                "landedInbox": d.landed_inbox,
                "spam": d.spam,
                "score": round(d.score or 0.0, 1),
            } for d in days],
        })
    return {"items": items, "total": len(items)}


@router.post("/run")
async def run_warmup(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    """Advance today's warmup for every active mailbox. Simulated: no network traffic."""
    mailboxes = (await db.execute(select(Mailbox).where(
        Mailbox.workspace_id == ctx.workspace.id,
        Mailbox.status == "active",
        Mailbox.warmup_enabled == True))).scalars().all()  # noqa: E712

    today = date.today()
    created = 0
    scores: list[float] = []

    for mb in mailboxes:
        row = (await db.execute(select(WarmupDay).where(
            WarmupDay.mailbox_id == mb.id, WarmupDay.day == today))).scalar_one_or_none()
        history = (await db.execute(select(WarmupDay).where(
            WarmupDay.mailbox_id == mb.id).order_by(WarmupDay.day.desc()).limit(1))).scalars().first()
        if row:
            row.sent = min((row.sent or 0) + 5, mb.daily_limit or 50)
            row.received = (row.received or 0) + 5
            row.landed_inbox = max(0, row.sent - (row.spam or 0))
        else:
            base = min(((history.sent if history else 4) + 3), mb.daily_limit or 50)
            row = WarmupDay(
                mailbox_id=mb.id,
                day=today,
                sent=base,
                received=base + 2,
                landed_inbox=max(0, base - max(0, (history.spam if history else 0) - 1)),
                spam=max(0, (history.spam if history else 1) - 1),
            )
            db.add(row)
            created += 1
        row.score = round(100.0 * (row.landed_inbox or 0) / max(row.sent or 1, 1), 1)
        mb.warmup_score = round(min(99.0, max(mb.warmup_score or 0.0, row.score)), 1)
        scores.append(mb.warmup_score)

    await db.commit()
    avg = round(sum(scores) / len(scores), 1) if scores else 0.0
    return {"mailboxes": len(mailboxes), "daysCreated": created, "avgScore": avg}
