from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Ctx, get_ctx
from ..database import get_db
from ..models import Campaign, InboxMessage, Send, Suppression
from ..serializers import iso, pct

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/overview")
async def overview(ctx: Ctx = Depends(get_ctx), db: AsyncSession = Depends(get_db)):
    ws_id = ctx.workspace.id

    async def count(*where) -> int:
        return (await db.execute(select(func.count(Send.id)).where(
            Send.workspace_id == ws_id, *where))).scalar_one()

    sent = await count(Send.status == "sent")
    opened = await count(Send.opened_at.isnot(None))
    clicked = await count(Send.clicked_at.isnot(None))
    replied = await count(Send.replied_at.isnot(None))
    bounced = await count(Send.status == "bounced")
    suppressed = (await db.execute(select(func.count(Suppression.id)).where(
        Suppression.workspace_id == ws_id))).scalar_one()

    campaigns = []
    for c in (await db.execute(select(Campaign).where(
            Campaign.workspace_id == ws_id).order_by(Campaign.created_at.desc()))).scalars().all():
        c_sent = (await db.execute(select(func.count(Send.id)).where(
            Send.campaign_id == c.id, Send.status == "sent"))).scalar_one()
        c_opened = (await db.execute(select(func.count(Send.id)).where(
            Send.campaign_id == c.id, Send.opened_at.isnot(None)))).scalar_one()
        c_clicked = (await db.execute(select(func.count(Send.id)).where(
            Send.campaign_id == c.id, Send.clicked_at.isnot(None)))).scalar_one()
        c_replied = (await db.execute(select(func.count(Send.id)).where(
            Send.campaign_id == c.id, Send.replied_at.isnot(None)))).scalar_one()
        campaigns.append({
            "id": c.id, "name": c.name, "status": c.status,
            "sent": c_sent, "opened": c_opened, "clicked": c_clicked, "replied": c_replied,
            "openRate": pct(c_opened, c_sent), "clickRate": pct(c_clicked, c_sent),
            "replyRate": pct(c_replied, c_sent),
        })

    daily = []
    for offset in range(6, -1, -1):
        day = date.today() - timedelta(days=offset)
        start = datetime.combine(day, datetime.min.time())
        end = start + timedelta(days=1)
        d_sent = (await db.execute(select(func.count(Send.id)).where(
            Send.workspace_id == ws_id, Send.status == "sent",
            Send.sent_at >= start, Send.sent_at < end))).scalar_one()
        d_opened = (await db.execute(select(func.count(Send.id)).where(
            Send.workspace_id == ws_id,
            Send.opened_at >= start, Send.opened_at < end))).scalar_one()
        d_replied = (await db.execute(select(func.count(Send.id)).where(
            Send.workspace_id == ws_id,
            Send.replied_at >= start, Send.replied_at < end))).scalar_one()
        daily.append({"day": iso(day), "sent": d_sent, "opened": d_opened, "replied": d_replied})

    msgs = (await db.execute(select(InboxMessage).where(
        InboxMessage.workspace_id == ws_id))).scalars().all()
    inbox = {
        "unread": sum(1 for m in msgs if not m.is_read),
        "interested": sum(1 for m in msgs if m.classification == "interested"),
        "notNow": sum(1 for m in msgs if m.classification == "not_now"),
        "unsubscribes": sum(1 for m in msgs if m.classification == "unsubscribe"),
        "bounces": sum(1 for m in msgs if m.classification == "bounce"),
    }

    return {
        "totals": {
            "sent": sent, "opened": opened, "clicked": clicked, "replied": replied,
            "bounced": bounced, "suppressed": suppressed,
            "openRate": pct(opened, sent), "clickRate": pct(clicked, sent),
            "replyRate": pct(replied, sent),
        },
        "campaigns": campaigns,
        "daily": daily,
        "inbox": inbox,
    }
