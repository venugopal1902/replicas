"""Full end-to-end suite for the Blotato-compatible engine.

Runs the real ASGI app against a fresh database. No network calls are required:
platform connectors fall back to the deterministic mock when credentials are absent.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from blotato_engine.config import settings
from blotato_engine.database import init_db

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name} {detail}".strip())
        print(f"  FAIL  {name} {detail}")


async def main() -> None:
    print("\n== boot ==")
    await init_db()

    from blotato_engine.main import app
    from httpx import ASGITransport

    transport = ASGITransport(app=app)
    H = {"blotato-api-key": settings.MASTER_API_KEY}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:

        print("\n== accounts and credits ==")
        r = await c.get("/health")
        check("health returns online", r.status_code == 200 and r.json()["status"] == "online")

        r = await c.get("/v2/users/me/accounts", headers=H)
        accounts = r.json().get("accounts", [])
        platforms = {a["platform"] for a in accounts}
        check("nine platforms connected", len(accounts) == 9, f"got {len(accounts)}")
        check(
            "all nine platforms present",
            platforms == {"instagram", "facebook", "twitter", "linkedin", "youtube",
                          "threads", "bluesky", "pinterest", "tiktok"},
            str(sorted(platforms)),
        )

        r = await c.get("/v2/credits", headers=H)
        check("credits unlimited for admin", r.json().get("isUnlimited") is True)

        ig = next(a for a in accounts if a["platform"] == "instagram")

        print("\n== publish ==")
        r = await c.post("/v2/posts", headers=H, json={
            "post": {
                "accountId": ig["id"],
                "content": {"text": "Pipeline check: publishing from the test suite.",
                            "mediaUrls": ["https://example.com/reel.mp4"]},
                "target": {"targetType": "instagram"},
            }
        })
        check("publish returns 200", r.status_code == 200, r.text[:120])
        pub = r.json()
        check("publish status is published", pub.get("status") == "published", str(pub.get("status")))
        check("publish returned a live url", bool(pub.get("url")), str(pub.get("url")))

        r = await c.get(f"/v2/posts/{pub['postSubmissionId']}", headers=H)
        check("post is retrievable by id", r.status_code == 200)

        print("\n== scheduling ==")
        r = await c.post("/v2/posts", headers=H, json={
            "post": {"accountId": ig["id"], "content": {"text": "Scheduled drop tomorrow."}},
            "scheduledTime": "2026-12-01T09:00:00+05:30",
        })
        sched = r.json()
        check("scheduled post accepted", sched.get("status") == "scheduled", str(sched.get("status")))

        r = await c.get("/v2/schedules", headers=H)
        check("schedule list includes it", any(
            i["postSubmissionId"] == sched["postSubmissionId"] for i in r.json()["items"]))

        r = await c.patch(f"/v2/schedules/{sched['postSubmissionId']}", headers=H,
                          json={"patch": {"scheduledTime": "2026-12-02T10:30:00+05:30"}})
        check("reschedule works", r.status_code == 200 and "2026-12-02" in str(r.json().get("scheduledTime")))

        r = await c.get("/v2/schedule-slots", headers=H)
        check("slots seeded", len(r.json()["items"]) >= 3, str(len(r.json()["items"])))

        r = await c.post("/v2/schedule-slots", headers=H, json={
            "accountId": ig["id"], "platform": "instagram", "dayOfWeek": 5,
            "hour": 20, "minute": 15, "timezone": "Asia/Kolkata"})
        slot = r.json()
        check("slot created", r.status_code == 200 and slot.get("time") == "20:15", str(slot))

        r = await c.get("/v2/schedule-slots/next", headers=H)
        check("next free slot resolves", r.status_code == 200 and bool(r.json().get("slotTime")))

        r = await c.delete(f"/v2/schedule-slots/{slot['id']}", headers=H)
        check("slot deleted", r.status_code == 200)

        r = await c.delete(f"/v2/schedules/{sched['postSubmissionId']}", headers=H)
        check("schedule cancelled", r.status_code == 200 and r.json().get("cancelled") is True)

        print("\n== media ==")
        files = {"file": ("probe.png", b"\x89PNG\r\n\x1a\n" + b"x" * 512, "image/png")}
        r = await c.post("/v2/media/upload", headers=H, files=files)
        check("media upload accepted", r.status_code == 200, r.text[:120])
        media = r.json()
        check("media public url returned", media.get("publicUrl", "").startswith("http"))
        check("media has content type", media.get("contentType") == "image/png")

        r = await c.get("/v2/media", headers=H)
        check("media listed", len(r.json()["items"]) >= 1)

        print("\n== comments and messages ==")
        r = await c.get("/v2/comments", headers=H)
        comments = r.json()["items"]
        check("comments seeded", len(comments) >= 4, str(len(comments)))

        reply_to = comments[0]["id"]
        r = await c.post(f"/v2/comments/{reply_to}/reply", headers=H, json={"text": "Sent you a DM."})
        check("comment reply stored", r.status_code == 200 and r.json()["isReplied"] is True)

        r = await c.get("/v2/comments?unreadOnly=true", headers=H)
        check("unread filter narrows results",
              all(not x["isReplied"] for x in r.json()["items"]))

        r = await c.get("/v2/messages", headers=H)
        threads = r.json()["items"]
        check("threads present", len(threads) >= 2, str(len(threads)))

        tid = threads[0]["threadId"]
        r = await c.get(f"/v2/messages/{tid}", headers=H)
        check("thread transcript loads", r.status_code == 200 and len(r.json()["messages"]) >= 1)

        r = await c.post("/v2/messages", headers=H, json={"threadId": tid, "text": "Testing outbound."})
        check("outbound message stored", r.status_code == 200 and r.json()["direction"] == "outbound")

        print("\n== dm automations ==")
        r = await c.post("/v2/dm-automations", headers=H, json={
            "accountId": ig["id"], "platform": "instagram", "name": "Price keyword",
            "dmMessage": "Here is the pricing link you asked for.",
            "triggers": [{"type": "comment-received", "keywords": ["price", "cost"], "isActive": True}],
            "commentReply": {"variants": [{"text": "Sent you a DM {{mention}}"},
                                          {"text": "Check your inbox {{mention}}"},
                                          {"text": "DM sent {{mention}}"}]},
        })
        check("automation created", r.status_code == 200, r.text[:120])
        auto = r.json()

        r = await c.post(f"/v2/dm-automations/{auto['id']}/test", headers=H,
                         json={"text": "what is the price?", "author": "tester"})
        run = r.json()
        mine = [m for m in run["matched"] if m["automationId"] == auto["id"]]
        check("keyword matched", len(mine) == 1, json.dumps(run)[:200])
        check("dm action executed",
              bool(mine) and any("dm sent" in a for a in mine[0]["actions"]),
              str(mine[0]["actions"] if mine else run))

        r = await c.post(f"/v2/dm-automations/{auto['id']}/test", headers=H,
                         json={"text": "totally unrelated comment", "author": "tester"})
        check("non matching keyword skipped", len(r.json()["matched"]) == 0)

        r = await c.get("/v2/webhook-events", headers=H)
        check("webhook events logged", len(r.json()["items"]) >= 1)

        r = await c.post("/webhooks/meta", json={
            "object": "instagram",
            "entry": [{"id": ig["id"], "changes": [{
                "field": "comments",
                "value": {"id": comments[1]["id"], "text": "cost?",
                          "from": {"username": "meta_user"}}}]}],
        })
        check("meta webhook ingress works",
              r.status_code == 200 and r.json()["received"] == 1, r.text[:200])

        print("\n== visuals ==")
        r = await c.get("/v2/videos/templates", headers=H)
        templates = r.json()["items"]
        check("templates listed", len(templates) >= 6, str(len(templates)))

        r = await c.post("/v2/videos/from-templates", headers=H, json={
            "templateId": "quote-card",
            "inputs": {"text": "Consistency beats intensity.", "author": "Venu"}})
        quote = r.json()
        check("quote card rendered", quote.get("status") == "ready", str(quote.get("error")))
        check("quote card has output", len(quote.get("outputs", [])) == 1)

        r = await c.post("/v2/videos/from-templates", headers=H, json={
            "templateId": "carousel",
            "inputs": {"slides": [
                {"title": "Why schedules beat motivation", "body": "Motivation is a mood."},
                {"title": "Pick three slots", "body": "Morning, lunch, evening."},
                {"title": "Fill them weekly", "body": "Batch on Sunday."}]}})
        carousel = r.json()
        check("carousel rendered three slides",
              carousel.get("status") == "ready" and len(carousel.get("outputs", [])) == 3,
              str(carousel.get("error")))

        if quote.get("outputs"):
            out_path = Path(settings.MEDIA_DIR) / "generated" / quote["outputs"][0].rsplit("/", 1)[-1]
            size = out_path.stat().st_size if out_path.exists() else 0
            check("quote png written to disk", out_path.exists() and size > 5000, f"{out_path.name} {size}B")

        r = await c.get("/v2/videos/creations", headers=H)
        check("visual history listed", len(r.json()["items"]) >= 2)

        print("\n== sources ==")
        r = await c.post("/v2/source-resolutions", headers=H, json={
            "sourceType": "text",
            "text": "Most creators fail because they post once and quit. "
                    "The fix is a schedule you can keep. Batch on Sunday. "
                    "Write three hooks. Ship one idea across every platform. "
                    "Then look at what earned saves, not likes."})
        src = r.json()
        check("text source resolved", src.get("status") == "ready", str(src.get("error")))
        check("source extracted a title", bool(src["extracted"].get("title")))

        r = await c.post(f"/v2/source-resolutions/{src['id']}/repurpose", headers=H,
                         json={"platforms": ["instagram", "twitter", "linkedin"]})
        drafts = r.json().get("drafts", [])
        check("repurpose produced three drafts", len(drafts) == 3, str(len(drafts)))
        check("drafts are platform shaped",
              all(d["text"] for d in drafts) and len({d["platform"] for d in drafts}) == 3)

        r = await c.get("/v2/source-resolutions", headers=H)
        check("sources listed", len(r.json()["items"]) >= 1)

        print("\n== analytics ==")
        r = await c.get("/v2/analytics", headers=H)
        ov = r.json()["overview"]
        check("overview totals computed", ov["totalPublished"] >= 4, str(ov["totalPublished"]))
        check("overview sums views", ov["totalViews"] > 0, str(ov["totalViews"]))
        check("engagement rate formatted", ov["averageEngagementRate"].endswith("%"))

        r = await c.get("/v2/analytics/top?metric=views&limit=3", headers=H)
        top = r.json()["items"]
        check("top posts ranked", len(top) == 3 and top[0]["views"] >= top[-1]["views"],
              str([t["views"] for t in top]))

        r = await c.get(f"/v2/analytics?postSubmissionId={top[0]['postSubmissionId']}", headers=H)
        detail = r.json()
        check("post metrics with history", bool(detail["metrics"]) and len(detail["history"]) >= 1)

        print("\n== mcp ==")
        r = await c.post("/mcp", headers=H, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = [t["name"] for t in r.json()["result"]["tools"]]
        check("mcp lists tools", len(tools) >= 5, str(len(tools)))

        r = await c.post("/mcp", headers=H, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "blotato_list_accounts", "arguments": {}}})
        check("mcp tool call executes", r.status_code == 200 and "content" in r.json()["result"])

        print("\n== auth ==")
        r = await c.get("/v2/users/me/accounts")
        check("missing key rejected", r.status_code == 401)
        r = await c.get("/v2/users/me/accounts", headers={"blotato-api-key": "bogus"})
        check("bad key rejected", r.status_code == 401)

    print("\n" + "=" * 62)
    print(f"PASSED {len(PASS)}   FAILED {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
    print("=" * 62)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
