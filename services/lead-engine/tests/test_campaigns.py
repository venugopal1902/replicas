"""Async harness for the campaigns module. No pytest.

Run: uv run python tests/test_campaigns.py
Prints PASS/FAIL per check and exits 1 on any failure.
"""
import asyncio
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# lead_engine.auth and lead_engine.database import each other at module level.
# Load campaigns first: it breaks that cycle when imported before either side.
from lead_engine.routes import campaigns  # noqa: E402
from lead_engine.auth import Ctx, get_ctx  # noqa: E402
from lead_engine.database import get_db  # noqa: E402
from lead_engine.models import Base, Contact, User, Workspace  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    line = f"{'PASS' if ok else 'FAIL'}  {name}"
    if detail and not ok:
        line += f"  — {detail}"
    print(line)


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        user = User(email="owner@example.com", password_hash="x", name="Owner")
        other_user = User(email="other@example.com", password_hash="x", name="Other")
        db.add_all([user, other_user])
        await db.commit()
        await db.refresh(user)
        await db.refresh(other_user)

        ws = Workspace(name="Mine", owner_id=user.id, recipients_limit=10, recipients_used=0)
        other_ws = Workspace(name="Theirs", owner_id=other_user.id, recipients_limit=10, recipients_used=0)
        db.add_all([ws, other_ws])
        await db.commit()
        await db.refresh(ws)
        await db.refresh(other_ws)

        c1 = Contact(
            workspace_id=ws.id, first_name="Ada", last_name="Lovelace",
            work_email="ada@example.com",
        )
        c2 = Contact(
            workspace_id=ws.id, first_name="Alan", last_name="Turing",
            direct_email="alan@example.com",
        )
        c_skip = Contact(workspace_id=ws.id, first_name="No", last_name="Email")
        c_foreign = Contact(
            workspace_id=other_ws.id, first_name="Eve", last_name="Other",
            work_email="eve@elsewhere.com",
        )
        db.add_all([c1, c2, c_skip, c_foreign])
        await db.commit()
        for obj in (c1, c2, c_skip, c_foreign):
            await db.refresh(obj)

        their_campaign_id = None
        from lead_engine.models import Campaign
        theirs = Campaign(workspace_id=other_ws.id, name="Secret", status="draft")
        db.add(theirs)
        await db.commit()
        await db.refresh(theirs)
        their_campaign_id = theirs.id

    state = {"ctx": Ctx(user, ws, "session")}

    async def override_db():
        async with Session() as session:
            yield session

    async def override_ctx():
        return state["ctx"]

    app = FastAPI()
    app.include_router(campaigns.router)
    app.include_router(campaigns.public_router)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_ctx] = override_ctx

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # create
        r = await client.post("/api/campaigns", json={
            "name": "Launch", "fromEmail": "me@example.com", "fromName": "Me",
        })
        body = r.json()
        check("create campaign", r.status_code == 200 and body.get("status") == "draft"
              and body.get("name") == "Launch", f"{r.status_code} {body}")
        cid = body["id"]

        # 3 steps
        for i, (subject, delay) in enumerate([("Hello", 0), ("Follow up", 3), ("Last call", 7)], start=1):
            r = await client.post(f"/api/campaigns/{cid}/steps", json={
                "subject": subject, "body": f"Body {i}", "delayDays": delay,
            })
            b = r.json()
            check(f"add step {i}", r.status_code == 200 and b.get("position") == i, f"{r.status_code} {b}")

        # recipients: 2 real + 1 no email + 1 foreign workspace contact
        before = state["ctx"].workspace.recipients_used
        r = await client.post(f"/api/campaigns/{cid}/recipients", json={
            "contactIds": [c1.id, c2.id, c_skip.id, c_foreign.id],
        })
        b = r.json()
        check("add recipients", r.status_code == 200 and b.get("added") == 2 and b.get("skipped") == 2,
              f"{r.status_code} {b}")

        async with Session() as db:
            fresh = await db.get(Workspace, ws.id)
            used = fresh.recipients_used
        check("quota increment", used == (before or 0) + 2, f"used={used} before={before}")

        # send
        r = await client.post(f"/api/campaigns/{cid}/send")
        b = r.json()
        check("send simulated", r.status_code == 200 and b.get("queued", 0) > 0 and b.get("simulated") is True,
              f"{r.status_code} {b}")

        # stats moved
        r = await client.get(f"/api/campaigns/{cid}")
        detail = r.json()
        stats = detail.get("stats", {})
        check("stats moved", r.status_code == 200 and stats.get("recipients") == 2 and stats.get("sent") == 2
              and len(detail.get("steps", [])) == 3,
              f"{r.status_code} {detail}")
        check("send flips draft to active", detail.get("campaign", {}).get("status") == "active",
              f"status={detail.get('campaign', {}).get('status')}")

        # tracking token
        from sqlalchemy import select
        from lead_engine.models import CampaignRecipient
        async with Session() as db:
            rec = (await db.execute(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == cid)
            )).scalars().first()
            token = rec.tracking_token
            open_before = rec.open_count or 0

        r = await client.get(f"/t/o/{token}.gif")
        check("tracking open returns gif",
              r.status_code == 200 and r.headers.get("content-type", "").startswith("image/gif")
              and r.content[:6] == b"GIF89a" and len(r.content) == 43,
              f"{r.status_code} {r.headers.get('content-type')} len={len(r.content)}")
        async with Session() as db:
            rec = (await db.execute(
                select(CampaignRecipient).where(CampaignRecipient.tracking_token == token)
            )).scalar_one()
            opened = rec.open_count or 0
        check("tracking open increments open_count", opened == open_before + 1, f"open_count={opened}")

        r = await client.get(f"/t/c/{token}", params={"u": "https://example.com/x"}, follow_redirects=False)
        check("tracking click redirects 302",
              r.status_code == 302 and r.headers.get("location") == "https://example.com/x",
              f"{r.status_code} loc={r.headers.get('location')}")
        async with Session() as db:
            rec = (await db.execute(
                select(CampaignRecipient).where(CampaignRecipient.tracking_token == token)
            )).scalar_one()
            clicks = rec.click_count or 0
        check("tracking click increments click_count", clicks == 1, f"click_count={clicks}")

        # javascript: must not be an open redirect
        r = await client.get(f"/t/c/{token}", params={"u": "javascript:alert(1)"}, follow_redirects=False)
        check("click rejects javascript scheme",
              r.status_code == 302 and r.headers.get("location") == "/",
              f"{r.status_code} loc={r.headers.get('location')}")

        r = await client.get("/t/o/not-a-real-token.gif")
        check("unknown token still returns gif 200",
              r.status_code == 200 and r.content[:6] == b"GIF89a",
              f"{r.status_code} {r.content[:6]!r}")

        # other workspace
        r = await client.get(f"/api/campaigns/{their_campaign_id}")
        check("other workspace campaign returns 404", r.status_code == 404, f"{r.status_code} {r.text}")

        # list reflects stats
        r = await client.get("/api/campaigns")
        items = r.json().get("items", [])
        mine = next((i for i in items if i["id"] == cid), None)
        check("list stats", mine is not None and mine["stepCount"] == 3 and mine["sent"] == 2
              and mine["opened"] >= 1 and mine["clicked"] >= 1,
              f"{items}")

        # events endpoint
        r = await client.get(f"/api/campaigns/{cid}/events")
        ev_items = r.json().get("items", [])
        check("events endpoint returns events", r.status_code == 200 and len(ev_items) >= 3
              and all("eventType" in ev and "email" in ev and "stepPosition" in ev for ev in ev_items),
              f"{r.status_code} {ev_items}")

        # patch campaign
        r = await client.patch(f"/api/campaigns/{cid}", json={"status": "invalid_status"})
        check("patch invalid status rejected", r.status_code == 400, f"{r.status_code}")
        r = await client.patch(f"/api/campaigns/{cid}", json={"name": "Renamed Campaign", "status": "paused"})
        check("patch valid update", r.status_code == 200 and r.json().get("name") == "Renamed Campaign"
              and r.json().get("status") == "paused", f"{r.status_code}")

        # test step limit (max 10)
        # cid currently has 3 steps, add 7 more -> 10 total
        for i in range(4, 11):
            r = await client.post(f"/api/campaigns/{cid}/steps", json={"subject": f"Step {i}", "body": "..."})
            assert r.status_code == 200
        # 11th step should fail
        r = await client.post(f"/api/campaigns/{cid}/steps", json={"subject": "Step 11", "body": "..."})
        check("step limit 10 enforced", r.status_code == 400, f"{r.status_code}")

        # test delete step
        steps_before = (await client.get(f"/api/campaigns/{cid}")).json().get("steps", [])
        del_step_id = steps_before[-1]["id"]
        r = await client.delete(f"/api/campaigns/{cid}/steps/{del_step_id}")
        check("delete step", r.status_code == 200 and r.json().get("deleted") is True, f"{r.status_code}")
        steps_after = (await client.get(f"/api/campaigns/{cid}")).json().get("steps", [])
        check("step count decreased", len(steps_after) == len(steps_before) - 1)

        # test send with 0 steps -> 400
        r = await client.post("/api/campaigns", json={"name": "No Steps Campaign"})
        empty_cid = r.json()["id"]
        r = await client.post(f"/api/campaigns/{empty_cid}/send")
        check("send with 0 steps rejected with 400", r.status_code == 400, f"{r.status_code}")

        # test quota exceeded -> 409
        # ws limit is 10, already used 2. Seed 10 new contacts and try to add all
        async with Session() as db:
            more_contacts = [
                Contact(workspace_id=ws.id, first_name=f"F{i}", last_name=f"L{i}", work_email=f"w{i}@ex.com")
                for i in range(10)
            ]
            db.add_all(more_contacts)
            await db.commit()
            more_cids = [c.id for c in more_contacts]
        r = await client.post(f"/api/campaigns/{empty_cid}/recipients", json={"contactIds": more_cids})
        check("quota exceeded returns 409", r.status_code == 409, f"{r.status_code} {r.text}")

        # test delete campaign
        r = await client.delete(f"/api/campaigns/{cid}")
        check("delete campaign", r.status_code == 200 and r.json().get("deleted") is True, f"{r.status_code}")
        r = await client.get(f"/api/campaigns/{cid}")
        check("deleted campaign returns 404", r.status_code == 404, f"{r.status_code}")

    await engine.dispose()
    failed = [name for name, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    except Exception:
        traceback.print_exc()
        code = 1
    sys.exit(code)
