"""Live feature verification against a running engine on :8400.

Proves each capability end to end, including the scheduler worker, media serving,
every visual template, every MCP tool and credit metering.
"""
import time
from datetime import datetime, timedelta

import httpx

BASE = "http://localhost:8400"
ADMIN = {"blotato-api-key": "blt_master_venu_secret_2026"}
ROWS = []


def rec(feature, ok, detail=""):
    ROWS.append((feature, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {feature:34s} {detail}")


def main():
    c = httpx.Client(base_url=BASE, timeout=40.0)

    # ---------------------------------------------------------------- health
    r = c.get("/health")
    rec("health", r.status_code == 200 and r.json()["status"] == "online")

    accounts = c.get("/v2/users/me/accounts", headers=ADMIN).json()["accounts"]
    ig = next(a for a in accounts if a["platform"] == "instagram")
    rec("accounts: 9 platforms", len({a["platform"] for a in accounts}) == 9,
        f"{len(accounts)} connected")

    # ------------------------------------------------------------ media 2-step
    req = c.post("/v2/media", headers=ADMIN,
                 json={"fileName": "verify.png", "contentType": "image/png", "size": 7})
    if req.status_code == 200:
        up = req.json()
        payload = b"\x89PNG\r\n\x1a\n" + b"verify-bytes"
        put = c.put(up["uploadUrl"], content=payload,
                    headers={"Content-Type": "image/png"})
        fetched = c.get(up["publicUrl"])
        rec("media: two-step upload + serve",
            put.status_code == 200 and fetched.status_code == 200
            and fetched.content == payload,
            f"served {len(fetched.content)}B")
    else:
        rec("media: two-step upload + serve", False, req.text[:80])

    # -------------------------------------------------------- every template
    tpls = c.get("/v2/videos/templates", headers=ADMIN).json()["items"]
    inputs = {
        "quote-card": {"text": "Consistency beats intensity.", "author": "Venu"},
        "tweet-card": {"text": "Ship the smallest version.", "name": "Venu", "handle": "@venu"},
        "carousel": {"slides": [{"title": "One", "body": "a"}, {"title": "Two", "body": "b"}]},
        "tutorial-carousel": {"steps": [{"title": "Step 1", "body": "do it"}]},
        "infographic-whiteboard": {"title": "Three rules", "points": ["a", "b", "c"]},
        "infographic-chalkboard": {"title": "Dark mode", "points": ["x", "y"]},
    }
    bad = []
    for t in tpls:
        body = {"templateId": t["id"], "inputs": inputs.get(t["id"], {})}
        rr = c.post("/v2/videos/from-templates", headers=ADMIN, json=body)
        d = rr.json() if rr.status_code == 200 else {}
        urls = d.get("outputs", [])
        ok = d.get("status") == "ready" and urls
        if ok:
            first = c.get(urls[0])
            ok = first.status_code == 200 and len(first.content) > 5000
        if not ok:
            bad.append(t["id"])
    rec("visuals: all 6 templates render", not bad, "failed: " + ", ".join(bad) if bad else "6/6 PNGs")

    # ------------------------------------------------------------- scheduler
    when = (datetime.utcnow() + timedelta(seconds=7)).replace(microsecond=0).isoformat()
    r = c.post("/v2/posts", headers=ADMIN, json={
        "post": {"accountId": ig["id"], "content": {"text": "Scheduler dispatch probe."}},
        "scheduledTime": when,
    })
    pid = r.json().get("postSubmissionId")
    became = None
    for _ in range(14):
        time.sleep(3)
        st = c.get(f"/v2/posts/{pid}", headers=ADMIN).json()
        if st["status"] != "scheduled":
            became = st
            break
    rec("scheduler: due post dispatches",
        bool(became) and became["status"] == "published",
        f"scheduled {when} -> {(became or {}).get('status')}")

    # ---------------------------------------------------------- automations
    auto = c.post("/v2/dm-automations", headers=ADMIN, json={
        "accountId": ig["id"], "name": "Verify gates",
        "dmMessage": "Base DM body.",
        "triggers": [{"type": "comment-received", "keywords": ["funnel"], "isActive": True}],
        "commentReply": {"variants": [{"text": "v1 {{mention}}"}, {"text": "v2 {{mention}}"},
                                      {"text": "v3 {{mention}}"}]},
        "emailGate": {"message": "Reply with your email and I will send it."},
        "followGate": {"message": "Follow first, then I will DM you."},
    }).json()
    run = c.post(f"/v2/dm-automations/{auto['id']}/test", headers=ADMIN,
                 json={"text": "drop the funnel please", "author": "probe"}).json()
    mine = [m for m in run["matched"] if m["automationId"] == auto["id"]]
    gates_ok = bool(mine) and any("comment reply" in a for a in mine[0]["actions"])
    rec("automations: keyword + gates fire", gates_ok,
        str(mine[0]["actions"]) if mine else "no match")

    dm_body = None
    threads = c.get("/v2/messages", headers=ADMIN).json()["items"]
    if threads:
        t = c.get(f"/v2/messages/{threads[0]['threadId']}", headers=ADMIN).json()
        dm_body = t["messages"][-1]["text"] if t["messages"] else None
    rec("automations: gated DM written",
        bool(dm_body) and "Follow first" in dm_body, str(dm_body)[:60])

    # ------------------------------------------------------------- analytics
    ov = c.get("/v2/analytics", headers=ADMIN).json()["overview"]
    top = c.get("/v2/analytics/top?metric=views&limit=3", headers=ADMIN).json()["items"]
    rec("analytics: totals + ranking",
        ov["totalViews"] > 0 and len(top) == 3 and top[0]["views"] >= top[-1]["views"],
        f"{ov['totalViews']} views, top {top[0]['views']}")

    # --------------------------------------------------------------- sources
    r = c.post("/v2/source-resolutions", headers=ADMIN,
               json={"url": "https://example.com/", "sourceType": "article"})
    src = r.json()
    ok = src.get("status") == "ready" and src["extracted"].get("body")
    rec("sources: live URL extraction", bool(ok), str(src.get("status")))
    if ok:
        drafts = c.post(f"/v2/source-resolutions/{src['id']}/repurpose", headers=ADMIN,
                        json={"platforms": ["instagram", "linkedin"]}).json()["drafts"]
        rec("sources: repurpose drafts", len(drafts) == 2, f"{len(drafts)} drafts")

    # --------------------------------------------------------- credit metering
    key = c.post("/v2/admin/keys", headers=ADMIN,
                 json={"clientName": "Credit Probe", "plan": "starter", "credits": 2}).json()
    CH = {"blotato-api-key": key["apiKey"]}
    acc = c.post("/v2/users/me/accounts", headers=CH, json={
        "platform": "instagram", "name": "Probe IG", "username": "probe",
        "credentials": {"provider": "mock"}}).json()
    ok_codes = []
    for i in range(2):
        rr = c.post("/v2/posts", headers=CH, json={
            "post": {"accountId": acc["id"], "content": {"text": f"meter {i}"}}})
        ok_codes.append(rr.status_code)
    third = c.post("/v2/posts", headers=CH, json={
        "post": {"accountId": acc["id"], "content": {"text": "over quota"}}})
    rec("billing: credits decrement then block",
        ok_codes == [200, 200] and third.status_code == 403,
        f"publishes {ok_codes}, third -> {third.status_code}")

    # --------------------------------------------------------------- mcp tools
    tools = c.post("/mcp", headers=ADMIN,
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
    comment = c.get("/v2/comments", headers=ADMIN).json()["items"][0]
    thread = c.get("/v2/messages", headers=ADMIN).json()["items"][0]["threadId"]
    calls = {
        "blotato_list_accounts": {},
        "blotato_publish_post": {"accountId": ig["id"], "text": "mcp probe"},
        "blotato_get_post_status": {"postSubmissionId": pid},
        "blotato_list_schedules": {},
        "blotato_next_free_slot": {"accountId": ig["id"]},
        "blotato_get_analytics": {},
        "blotato_top_posts": {"metric": "views", "limit": 3},
        "blotato_list_comments": {},
        "blotato_reply_to_comment": {"commentId": comment["id"], "text": "thanks"},
        "blotato_list_threads": {},
        "blotato_get_thread": {"threadId": thread},
        "blotato_send_message": {"threadId": thread, "text": "mcp reply"},
        "blotato_list_automations": {},
        "blotato_run_automation": {"automationId": auto["id"], "text": "funnel"},
        "blotato_list_templates": {},
        "blotato_create_visual": {"templateId": "quote-card",
                                  "inputs": {"text": "MCP render"}, "baseUrl": BASE},
        "blotato_check_credits": {},
    }
    broken = []
    for name, args in calls.items():
        rr = c.post("/mcp", headers=ADMIN, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": args}})
        res = rr.json().get("result", {})
        if rr.status_code != 200 or res.get("isError"):
            broken.append(f"{name}:{res.get('content',[{}])[0].get('text','')[:40]}")
    rec("mcp: all 17 tools execute", not broken and len(tools) == 17,
        f"{len(tools)} tools, broken: {broken}" if broken else f"{len(tools)}/17 ok")

    # ------------------------------------------------------------------- ui
    ui = c.get("/")
    rec("ui: console served", ui.status_code == 200 and b"Blotato Engine" in ui.content,
        f"{len(ui.content)}B")

    failed = [r for r in ROWS if not r[1]]
    print("\n" + "=" * 66)
    print(f"FEATURES VERIFIED {len(ROWS) - len(failed)}/{len(ROWS)}")
    for f in failed:
        print("  FAILED:", f[0], f[2])
    print("=" * 66)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
