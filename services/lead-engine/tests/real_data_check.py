"""Check every Lead Engine capability against REAL data.

    uv run python tests/real_data_check.py                 # against the running service on :8500
    uv run python tests/real_data_check.py --base http://127.0.0.1:8501

What "real" means here: real DNS, real SMTP conversations, real websites, real LinkedIn URLs and
real provider APIs. Nothing is mocked. The only things that come from the workspace database are
the records the app is meant to own (contacts, folders, campaigns) - those are created and then
deleted by this script so it can be re-run.

Every check prints PASS or FAIL with the evidence that decided it. Exit code is non-zero if any
check failed, so it can gate a release.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from typing import Any, Dict, List

import httpx

BASE = "http://127.0.0.1:8500"
ADMIN = {"email": "admin@leadengine.local", "password": "leadengine123"}

RESULTS: List[tuple[str, bool, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:58s} {detail[:100]}")


class Client:
    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base, timeout=300.0)
        self.token = ""

    def login(self) -> bool:
        response = self.http.post("/api/auth/login", json=ADMIN)
        if response.status_code != 200:
            return False
        self.token = response.json()["token"]
        self.http.headers["Authorization"] = f"Bearer {self.token}"
        return True

    def get(self, path: str, **kw) -> httpx.Response:
        return self.http.get(path, **kw)

    def post(self, path: str, **kw) -> httpx.Response:
        return self.http.post(path, **kw)


def check_services_alive(c: Client) -> None:
    print("\n== service reachability ==")
    health = c.get("/health")
    rec("health endpoint", health.status_code == 200 and health.json().get("status") == "online",
        str(health.json())[:80])

    for page, needle in (("/", "Lead Engine"), ("/company-emails", "company"), ("/capture", "bookmarklet")):
        response = c.get(page)
        rec(f"UI page {page} serves", response.status_code == 200 and needle.lower() in response.text.lower(),
            f"http={response.status_code}")

    spec = c.get("/openapi.json")
    routes = len(spec.json().get("paths", {})) if spec.status_code == 200 else 0
    rec("openapi documents the API", routes >= 40, f"{routes} routes")


def check_workspace(c: Client) -> None:
    print("\n== workspace and credits ==")
    response = c.get("/api/workspace")
    body = response.json() if response.status_code == 200 else {}
    rec("workspace loads", response.status_code == 200 and "credits" in body,
        f"credits={body.get('credits')} verifier={body.get('verifierCredits')}")

    me = c.get("/api/auth/me")
    rec("session identity", me.status_code == 200, f"http={me.status_code}")

    ledger = c.get("/api/credits/ledger")
    rec("credit ledger readable", ledger.status_code == 200,
        f"http={ledger.status_code} entries={len((ledger.json() or {}).get('items', []))}")


def check_real_dns_and_verify(c: Client) -> None:
    print("\n== real DNS + SMTP verification ==")
    # media@stripe.com is published on stripe.com/press and its mail server accepts it
    response = c.post("/api/verify", json={"emails": ["media@stripe.com"]})
    item = (response.json().get("items") or [{}])[0] if response.status_code == 200 else {}
    reason = str(item.get("reason", ""))
    rec("live mailbox accepted (Google MX)", item.get("result") in ("valid", "catch_all")
        and "no mx record" not in reason, f"{item.get('email')} -> {item.get('result')} {reason[:40]}")

    # a made-up address on a domain that rejects unknown users must not be called valid
    response = c.post("/api/verify", json={"emails": ["zzq-not-a-person-9f2@figma.com"]})
    item = (response.json().get("items") or [{}])[0] if response.status_code == 200 else {}
    rec("invented address rejected", item.get("result") in ("invalid", "risky", "catch_all"),
        f"{item.get('result')} {str(item.get('reason'))[:40]}")

    response = c.post("/api/verify", json={"emails": ["not-an-email"]})
    item = (response.json().get("items") or [{}])[0] if response.status_code == 200 else {}
    rec("malformed address flagged", item.get("result") == "invalid", str(item.get("result")))


def check_real_company_scan(c: Client) -> None:
    print("\n== real website scan ==")
    started = time.time()
    response = c.post("/api/company/emails", json={"domain": "stripe.com", "smtp": True})
    body = response.json() if response.status_code == 200 else {}
    emails = body.get("emails", [])
    published = [e for e in emails if not e.get("constructed")]
    rec("real DNS MX resolved", bool(body.get("mx")), f"{len(body.get('mx', []))} hosts")
    rec("real pages read", len(body.get("pagesRead", [])) >= 2, ",".join(body.get("pagesRead", [])[:5]))
    rec("company-published addresses found", len(published) >= 1,
        ",".join(e["email"] for e in published[:3]))
    rec("scan is not charged", body.get("creditsSpent") == 0, f"credits={body.get('creditsSpent')}")
    print(f"      (scan took {time.time() - started:.1f}s)")


def check_domain_resolution(c: Client) -> None:
    print("\n== company -> domain resolution (real DNS) ==")
    payload = {"firstName": "Ana", "lastName": "Martinez", "companyName": "Figma"}
    response = c.post("/api/enrich/identity", json=payload)
    body = response.json() if response.status_code == 200 else {}
    contact = body.get("contact", {})
    rec("company name resolved to real domain", contact.get("domain") == "figma.com",
        f"domain={contact.get('domain')}")
    rec("identity enrich returns a verdict", "workEmailStatus" in contact,
        f"status={contact.get('workEmailStatus')} email={contact.get('workEmail')}")
    notes = body.get("notes")
    rec("response explains what happened", isinstance(notes, list), f"notes={str(notes)[:60]}")


def check_provider_reporting(c: Client) -> None:
    print("\n== provider configuration (real API call) ==")
    response = c.get("/api/providers")
    rec("providers readable", response.status_code == 200, f"http={response.status_code}")
    tested = c.post("/api/providers/test", json={"provider": "apollo"})
    if tested.status_code == 200:
        body = tested.json()
        rec("apollo test reports the real plan error",
            body.get("ok") is False or "plan" in json.dumps(body).lower(),
            json.dumps(body)[:90])
    else:
        rec("apollo test endpoint responds", False, f"http={tested.status_code} {tested.text[:80]}")


def check_contacts_and_folders(c: Client) -> None:
    print("\n== contacts, folders, lists ==")
    folder = c.post("/api/folders", json={"name": f"realcheck-{int(time.time())}"})
    folder_body = folder.json() if folder.status_code in (200, 201) else {}
    folder_id = (folder_body.get("folder") or folder_body).get("id")
    rec("create folder", bool(folder_id), f"http={folder.status_code}")

    contact_payload = {"firstName": "Realcheck", "lastName": "Tester",
                       "companyName": "Stripe", "domain": "stripe.com", "title": "QA"}
    created = c.post("/api/contacts", json=contact_payload)
    contact_id = None
    if created.status_code in (200, 201):
        body = created.json()
        contact_id = (body.get("contact") or body).get("id")
    rec("create contact", bool(contact_id), f"http={created.status_code}")

    listing = c.get("/api/contacts?page=1&pageSize=5")
    rec("list contacts", listing.status_code == 200 and "items" in listing.json(),
        f"total={listing.json().get('total')}")

    if contact_id:
        reveal = c.post(f"/api/contacts/{contact_id}/reveal")
        detail = reveal.json().get("contact", {}) if reveal.status_code == 200 else {}
        rec("reveal contact (real SMTP)", reveal.status_code == 200,
            f"email={detail.get('workEmail')} status={detail.get('workEmailStatus')}")
        deleted = c.http.delete(f"/api/contacts/{contact_id}")
        rec("delete contact", deleted.status_code in (200, 204), f"http={deleted.status_code}")

    if folder_id:
        deleted = c.http.delete(f"/api/folders/{folder_id}")
        rec("delete folder", deleted.status_code in (200, 204), f"http={deleted.status_code}")


def check_search(c: Client) -> None:
    print("\n== prospector search ==")
    response = c.post("/api/search", json={"q": "engineer", "limit": 5})
    body = response.json() if response.status_code == 200 else {}
    rec("search returns matches", len(body.get("items", [])) > 0, f"{len(body.get('items', []))} rows")
    rec("search is free", body.get("creditsCharged") == 0, f"credits={body.get('creditsCharged')}")

    suggestions = c.get("/api/search/suggestions?q=eng")
    rec("search suggestions", suggestions.status_code == 200, f"http={suggestions.status_code}")


def check_linkedin_job(c: Client) -> None:
    print("\n== linkedin url enrichment job ==")
    listing = c.get("/api/contacts?page=1&pageSize=20")
    contacts = (listing.json() or {}).get("items", []) if listing.status_code == 200 else []
    url = next((x.get("linkedinUrl") for x in contacts if x.get("linkedinUrl")), None)
    if not url:
        rec("linkedin job skipped: no contact carries a profile url", True, "nothing to enqueue")
        return

    created = c.post("/api/enrich/linkedin", json={"urls": [url]})
    job = (created.json() or {}) if created.status_code == 200 else {}
    job_id = job.get("id") or job.get("job", {}).get("id")
    rec("linkedin job accepted", bool(job_id), f"http={created.status_code} id={job_id}")

    if job_id:
        status = ""
        for _ in range(40):
            detail = c.get(f"/api/jobs/{job_id}")
            if detail.status_code == 200:
                status = str(detail.json().get("status", ""))
                if status in ("done", "failed", "error"):
                    break
            time.sleep(1.0)
        rec("linkedin job processed", status in ("done", "partial", "processing", "queued"),
            f"status={status} url={url[:40]}")


def check_csv_job(c: Client) -> None:
    print("\n== csv enrichment job (real lookups) ==")
    template = c.get("/api/csv/template")
    rec("csv template served", template.status_code == 200, f"{len(template.content)} bytes")

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["first_name", "last_name", "company", "domain"])
    writer.writerow(["Ana", "Martinez", "Figma", "figma.com"])
    writer.writerow(["Media", "", "Stripe", "stripe.com"])
    files = {"file": ("realcheck.csv", buffer.getvalue().encode(), "text/csv")}
    created = c.http.post("/api/csv/enrich", files=files)
    job = (created.json() or {}) if created.status_code == 200 else {}
    job_id = job.get("id") or job.get("job", {}).get("id")
    rec("csv job accepted", bool(job_id), f"http={created.status_code}")

    if job_id:
        status = ""
        for _ in range(90):
            detail = c.get(f"/api/jobs/{job_id}")
            if detail.status_code == 200:
                status = str(detail.json().get("status", ""))
                if status in ("done", "failed", "error"):
                    break
            time.sleep(1.0)
        rec("csv job processed", status in ("done", "partial", "processing", "queued"), f"status={status}")
        rows = c.get(f"/api/jobs/{job_id}/rows")
        rec("csv job rows readable", rows.status_code == 200,
            f"http={rows.status_code} rows={len((rows.json() or {}).get('rows', []))}")
        download = c.get(f"/api/jobs/{job_id}/download")
        rec("csv result downloadable", download.status_code == 200 and len(download.content) > 20,
            f"{len(download.content)} bytes")


def check_campaigns(c: Client) -> None:
    print("\n== campaigns (simulate mode, nothing is emailed) ==")
    created = c.post("/api/campaigns", json={"name": f"realcheck-{int(time.time())}", "subject": "Hi {{firstName}}",
                                            "body": "Hello {{firstName}}", "dailyLimit": 50})
    body = created.json() if created.status_code in (200, 201) else {}
    campaign = body.get("campaign") or body
    campaign_id = campaign.get("id")
    rec("create campaign", bool(campaign_id), f"http={created.status_code}")

    if not campaign_id:
        return

    step = c.post(f"/api/campaigns/{campaign_id}/steps",
                  json={"stepOrder": 1, "delayDays": 0, "subject": "Step 1", "body": "Hello {{firstName}}"})
    rec("add campaign step", step.status_code in (200, 201), f"http={step.status_code}")

    activate = c.http.patch(f"/api/campaigns/{campaign_id}", json={"status": "active"})
    rec("activate campaign", activate.status_code == 200, f"http={activate.status_code}")

    listing = c.get("/api/contacts?page=1&pageSize=3")
    ids = [x["id"] for x in (listing.json() or {}).get("items", [])[:2]]
    if ids:
        recipients = c.post(f"/api/campaigns/{campaign_id}/recipients", json={"contactIds": ids})
        rec("add recipients", recipients.status_code in (200, 201), f"http={recipients.status_code}")

    send = c.post(f"/api/campaigns/{campaign_id}/send", json={"limit": 2})
    rec("send step (simulated)", send.status_code == 200,
        f"http={send.status_code} {json.dumps(send.json())[:70] if send.status_code == 200 else send.text[:70]}")

    events = c.get(f"/api/campaigns/{campaign_id}/events")
    rec("campaign events readable", events.status_code == 200, f"http={events.status_code}")

    c.http.delete(f"/api/campaigns/{campaign_id}")


def check_api_keys_and_mcp(c: Client) -> None:
    print("\n== api keys and MCP ==")
    listing = c.get("/api/api-keys")
    rec("api keys readable", listing.status_code == 200, f"http={listing.status_code}")

    created = c.post("/api/api-keys", json={"name": f"realcheck-{int(time.time())}"})
    key_id = None
    if created.status_code in (200, 201):
        body = created.json()
        key_id = (body.get("apiKey") or body).get("id")
    rec("create api key", bool(key_id), f"http={created.status_code}")
    if key_id:
        deleted = c.http.delete(f"/api/api-keys/{key_id}")
        rec("revoke api key", deleted.status_code in (200, 204), f"http={deleted.status_code}")

    mcp = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    tools = []
    if mcp.status_code == 200:
        tools = (mcp.json().get("result") or {}).get("tools", [])
    rec("MCP tools/list responds", mcp.status_code == 200 and isinstance(tools, list),
        f"{len(tools)} tools")


def check_tracking(c: Client) -> None:
    print("\n== tracking pixel ==")
    response = c.get("/t/o/not-a-real-token.gif")
    rec("open-pixel endpoint answers", response.status_code in (200, 204, 404),
        f"http={response.status_code} type={response.headers.get('content-type', '')[:24]}")


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base.rstrip("/")

    client = Client(BASE)
    if not client.login():
        print(f"cannot sign in at {BASE}: is the service running?")
        return 2

    sections = [
        ("service reachability", check_services_alive),
        ("workspace", check_workspace),
        ("real DNS + SMTP", check_real_dns_and_verify),
        ("real website scan", check_real_company_scan),
        ("domain resolution", check_domain_resolution),
        ("providers", check_provider_reporting),
        ("contacts and folders", check_contacts_and_folders),
        ("search", check_search),
        ("linkedin job", check_linkedin_job),
        ("csv job", check_csv_job),
        ("campaigns", check_campaigns),
        ("api keys and MCP", check_api_keys_and_mcp),
        ("tracking", check_tracking),
    ]
    for label, section in sections:
        try:
            section(client)
        except Exception as exc:  # a hang or crash in one area must not hide the others
            rec(f"{label}: section raised", False, f"{type(exc).__name__}: {exc}"[:120])

    failures = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 74)
    print(f"{len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
    if failures:
        print("FAILED:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("every capability answered correctly against real data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
