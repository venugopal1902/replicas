"""End-to-end feature verification for Lead Engine against a running server on :8500."""
import time
import httpx

BASE = "http://localhost:8500"
ROWS = []


def rec(name, ok, detail=""):
    ROWS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:38s} {detail}")


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=90.0)

    # ---------------------------------------------------------------- health
    h = c.get("/health")
    rec("health", h.status_code == 200 and h.json()["status"] == "online")
    rec("campaigns module loaded", h.json().get("campaignsLoaded") is True)

    # ------------------------------------------------------------------ auth
    bad = c.post("/api/auth/login", json={"email": "admin@leadengine.local", "password": "wrong"})
    rec("bad password rejected", bad.status_code == 401)

    r = c.post("/api/auth/login", json={"email": "admin@leadengine.local", "password": "leadengine123"})
    if r.status_code != 200:
        rec("login", False, r.text[:120])
        return 1
    tok = r.json()["token"]
    H = {"Authorization": f"Bearer {tok}"}
    rec("login", True, r.json()["user"]["email"])

    me = c.get("/api/auth/me", headers=H).json()
    start_credits = me["workspace"]["credits"]
    rec("workspace credits loaded", start_credits > 0, f"{start_credits} credits")

    noauth = httpx.get(BASE + "/api/contacts", timeout=20.0)
    rec("unauthenticated blocked", noauth.status_code == 401, str(noauth.status_code))

    # ---------------------------------------------------------------- search
    s = c.post("/api/search", headers=H, json={"filters": {}, "pageSize": 50})
    rec("search returns contacts", s.status_code == 200 and s.json()["total"] >= 40,
        f"{s.json().get('total')} contacts")
    rec("search costs nothing", s.json().get("creditsCharged") == 0)

    items = s.json()["items"]
    masked = [i for i in items if not i["revealed"]]
    rec("unrevealed contacts are masked", all("***" in (i["workEmailMasked"] or "***") for i in masked[:5])
        if masked else False, f"{len(masked)} masked")

    filtered = c.post("/api/search", headers=H,
                      json={"filters": {"location": "London"}, "pageSize": 50}).json()
    rec("filter by location works", filtered["total"] > 0 and filtered["total"] < s.json()["total"],
        f"{filtered['total']} in London")

    prompt = c.post("/api/search", headers=H,
                    json={"prompt": "Head of Talent at fintech in London", "pageSize": 10}).json()
    rec("natural language prompt parses", prompt["total"] >= 0,
        f"filters={prompt.get('filtersApplied')}")

    sugg = c.get("/api/search/suggestions", headers=H).json()
    rec("suggestions available", len(sugg["companies"]) > 5 and len(sugg["titles"]) > 3,
        f"{len(sugg['companies'])} companies")

    # ---------------------------------------------------------------- reveal
    # Create a fresh contact on a domain with real MX so the charge path is deterministic
    fresh = c.post("/api/contacts", headers=H, json={
        "firstName": "Charge", "lastName": f"Probe{int(time.time())}",
        "companyName": "Stripe", "domain": "stripe.com", "title": "Head of Sales"}).json()
    mx_contact = {"id": fresh["id"], "companyName": "Stripe"}
    rec("fresh contact created for charge test", "id" in fresh, fresh.get("id", "")[:8])

    if mx_contact:
        before = c.get("/api/workspace", headers=H).json()["credits"]
        rv = c.post(f"/api/contacts/{mx_contact['id']}/reveal", headers=H, timeout=120)
        data = rv.json()
        contact = data.get("contact", {})
        rec("reveal returns a contact", rv.status_code == 200 and bool(contact.get("workEmail")),
            str(contact.get("workEmail")))
        rec("revealed email has a status", contact.get("workEmailStatus") in
            ("verified", "risky", "catch_all", "unknown"), str(contact.get("workEmailStatus")))
        rec("revealed email has a score", bool(contact.get("workEmailScore")),
            str(contact.get("workEmailScore")))
        rec("phone not invented", contact.get("phone") is None and contact.get("phoneSource") is None,
            str(contact.get("phone")))
        after = c.get("/api/workspace", headers=H).json()["credits"]
        rec("one credit charged on success", before - after == 1, f"{before} -> {after}")

        again = c.post(f"/api/contacts/{mx_contact['id']}/reveal", headers=H).json()
        rec("re-reveal charges nothing", again.get("creditsSpent") == 0)

    # A contact on a domain with no MX: nothing can be found, so no credit is taken
    fake_domains = {"Northvale", "Kestrelpay", "Loambridge", "Quillbyte", "Fernpay",
                    "Tallyfin", "Brackenpay", "Mossgrove"}
    fake = next((i for i in items
                 if i["companyName"] in fake_domains and not i["revealed"]), None)
    if fake:
        before = c.get("/api/workspace", headers=H).json()["credits"]
        rv = c.post(f"/api/contacts/{fake['id']}/reveal", headers=H, timeout=120).json()
        after = c.get("/api/workspace", headers=H).json()["credits"]
        rec("unresolvable domain charges nothing",
            rv.get("creditsSpent") == 0 and before == after,
            f"{fake['companyName']} status={rv['contact'].get('workEmailStatus')}")
        rec("unresolvable domain reports honestly",
            rv["contact"].get("workEmailStatus") in ("invalid", "unavailable", "unknown"),
            str(rv["contact"].get("workEmailStatus")))

    # --------------------------------------------------------------- folders
    f = c.post("/api/folders", headers=H, json={"name": "Q4 Prospects", "color": "#7c9cff"}).json()
    rec("folder created", "id" in f, f.get("name"))
    add = c.post(f"/api/folders/{f['id']}/contacts", headers=H,
                 json={"contactIds": [items[0]["id"], items[1]["id"]]}).json()
    rec("contacts added to folder", add["added"] == 2, str(add))
    listed = c.get(f"/api/folders/{f['id']}/contacts", headers=H).json()
    rec("folder contents listed", len(listed["items"]) == 2)
    counts = c.get("/api/folders", headers=H).json()["items"]
    rec("folder count reflects contents", any(x["id"] == f["id"] and x["count"] == 2 for x in counts))

    # ------------------------------------------------------ identity enrich
    ident = c.post("/api/enrich/identity", headers=H, json={
        "firstName": "Elena", "lastName": "Petrova", "companyName": "Figma",
        "title": "Head of Design"}, timeout=120).json()
    rec("identity enrichment runs", ident.get("contact", {}).get("revealed") is True,
        str(ident.get("contact", {}).get("workEmail")))
    rec("identity domain resolved", ident.get("contact", {}).get("domain") == "figma.com",
        str(ident.get("contact", {}).get("domain")))

    # ------------------------------------------------------- reverse email
    email = (ident.get("contact") or {}).get("workEmail")
    if email:
        rev = c.post("/api/enrich/reverse-email", headers=H, json={"email": email}).json()
        rec("reverse email resolves a person", rev.get("contact") is not None,
            str((rev.get("contact") or {}).get("name")))

    # ------------------------------------------------------------------- csv
    csv_body = "linkedin_url,company\nhttps://www.linkedin.com/in/maya-okafor-1a2b,Stripe\n"
    up = c.post("/api/csv/enrich", headers=H,
                files={"file": ("leads.csv", csv_body.encode(), "text/csv")},
                data={"mode": "url"})
    rec("csv job accepted", up.status_code == 200, up.text[:100])
    job_id = up.json().get("id")

    status = "queued"
    for _ in range(30):
        time.sleep(2)
        j = c.get(f"/api/jobs/{job_id}", headers=H).json()
        status = j["status"]
        if status in ("done", "failed"):
            break
    rec("csv job completes", status == "done", f"status={status} processed={j.get('processed')}")
    if status == "done":
        dl = c.get(f"/api/jobs/{job_id}/download", headers=H)
        rec("csv result downloadable", dl.status_code == 200 and b"work_email" in dl.content,
            f"{len(dl.content)}B")

    tmpl = c.get("/api/csv/template?mode=lead", headers=H)
    rec("csv template served", tmpl.status_code == 200 and b"first_name" in tmpl.content)

    # --------------------------------------------------------------- verifier
    v = c.post("/api/verify", headers=H, json={
        "emails": ["not-an-email", "info@stripe.com", "someone@gmail.com"]})
    rec("verifier responds", v.status_code == 200, v.text[:80])
    results = {i["email"]: i["result"] for i in v.json().get("items", [])}
    rec("verifier flags malformed", results.get("not-an-email") == "invalid", str(results))
    rec("verifier flags role account", results.get("info@stripe.com") == "risky", str(results))
    rec("verifier checks a live domain", results.get("someone@gmail.com") in
        ("risky", "catch_all", "valid", "unknown"), str(results.get("someone@gmail.com")))

    # -------------------------------------------------------------- campaigns
    camp = c.post("/api/campaigns", headers=H, json={
        "name": "Q4 outreach", "fromEmail": "sales@example.com", "fromName": "Venu"}).json()
    rec("campaign created", "id" in camp, camp.get("name"))

    for i, subj in enumerate(["Quick question", "Following up", "Last note"], start=1):
        c.post(f"/api/campaigns/{camp['id']}/steps", headers=H,
               json={"subject": subj, "body": f"Step {i} body", "delayDays": i - 1})
    steps = c.get(f"/api/campaigns/{camp['id']}", headers=H).json()["steps"]
    rec("three campaign steps added", len(steps) == 3)

    revealed_ids = [i["id"] for i in items if i["revealed"]][:3]
    rc = c.post(f"/api/campaigns/{camp['id']}/recipients", headers=H,
                json={"contactIds": revealed_ids}).json()
    rec("campaign recipients added", rc.get("added", 0) >= 1, str(rc))

    send = c.post(f"/api/campaigns/{camp['id']}/send", headers=H).json()
    rec("send is simulated, never real", send.get("simulated") is True, str(send))
    rec("send queued recipients", send.get("queued", 0) >= 1, str(send.get("queued")))

    stats = c.get(f"/api/campaigns/{camp['id']}", headers=H).json()["stats"]
    rec("campaign stats moved", stats["sent"] >= 1, str(stats))

    # tracking pixel
    rcpts = c.get(f"/api/campaigns/{camp['id']}/events", headers=H).json()["items"]
    rec("send events recorded", len(rcpts) >= 1, f"{len(rcpts)} events")

    # find a token via the DB-backed events is not exposed; use the recipients listing through events
    # validate the public pixel endpoint shape with an unknown token
    px = c.get("/t/o/unknowntoken.gif")
    rec("tracking pixel always returns gif", px.status_code == 200 and px.headers["content-type"].startswith("image/gif"),
        px.headers.get("content-type"))

    # ---------------------------------------------------------------- api keys
    k = c.post("/api/api-keys", headers=H, json={"label": "probe"}).json()
    rec("api key minted", k.get("key", "").startswith("lke_live_"), k.get("label"))
    keys = c.get("/api/api-keys", headers=H).json()["items"]
    rec("api keys listed", any(x["id"] == k["id"] for x in keys))
    # the key must authenticate
    ka = c.get("/api/workspace", headers={"x-api-key": k["key"]})
    rec("minted key authenticates", ka.status_code == 200)
    c.delete(f"/api/api-keys/{k['id']}", headers=H)
    rec("api key deleted", all(x["id"] != k["id"] for x in c.get("/api/api-keys", headers=H).json()["items"]))

    # -------------------------------------------------------------------- mcp
    tl = c.post("/mcp", headers=H, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    tools = [t["name"] for t in tl["result"]["tools"]]
    rec("mcp exposes six tools", len(tools) == 6, ", ".join(tools))

    call = c.post("/mcp", headers=H, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "search_prospects", "arguments": {"filters": {"location": "London"}}}}).json()
    rec("mcp search executes", "content" in call["result"])

    credits_call = c.post("/mcp", headers=H, json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "check_credits", "arguments": {}}}).json()
    rec("mcp credits tool executes", "content" in credits_call["result"])

    # ---------------------------------------------------------------- ledger
    ledger = c.get("/api/credits/ledger", headers=H).json()["items"]
    rec("credit ledger records spend", len(ledger) >= 1,
        f"{sum(1 for e in ledger if e['delta'] < 0)} debits")

    failed = [r for r in ROWS if not r[1]]
    print("\n" + "=" * 70)
    print(f"FEATURES VERIFIED {len(ROWS) - len(failed)}/{len(ROWS)}")
    for f_ in failed:
        print("  FAILED:", f_[0], f_[2])
    print("=" * 70)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
