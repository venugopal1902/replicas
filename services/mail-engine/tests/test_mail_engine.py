"""Live API acceptance test suite for Mail Engine against http://localhost:8600.
Requires a running server. Skip gracefully with exit code 2 if server is not reachable.
"""
from __future__ import annotations

import os
import secrets
import sys
import time
from typing import Any

import httpx

BASE_URL = os.environ.get("MAIL_BASE", "http://localhost:8640").rstrip("/")
CHECKS: list[tuple[str, bool, str]] = []
ALL_STATUS_CODES: list[int] = []


def record_check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((name, passed, detail))
    status = "PASS" if passed else "FAIL"
    suffix = f" :: {detail}" if detail else ""
    print(f"[{status}] {name}{suffix}")


class TrackedClient:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = self._client.request(method, path, **kwargs)
        ALL_STATUS_CODES.append(resp.status_code)
        return resp

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)

    def close(self) -> None:
        self._client.close()


def main() -> int:
    # 0. Check reachability before running tests
    probe_client = httpx.Client(base_url=BASE_URL, timeout=5.0)
    reachable = False
    probe_err = None
    for _ in range(3):
        try:
            r = probe_client.get("/health")
            if r.status_code == 200 and r.json().get("service") == "Mail Engine":
                reachable = True
                break
            elif r.status_code == 200 and r.json().get("service") != "Mail Engine":
                probe_err = f"port answered with different service: {r.json().get('service')!r}"
            elif r.status_code < 500:
                # If service name is slightly different, still check if it's online
                if r.json().get("status") == "online" and "mail" in str(r.json()).lower():
                    reachable = True
                    break
                probe_err = f"unexpected response: {r.text[:120]}"
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError, httpx.RequestError) as exc:
            probe_err = str(exc)
            time.sleep(0.5)
    probe_client.close()

    if not reachable:
        print(f"Mail Engine server at {BASE_URL} is not reachable ({probe_err}). Skipping live checks.")
        sys.exit(2)

    client = TrackedClient(BASE_URL)

    try:
        # 1. GET /health 200 and status online
        r1 = client.get("/health")
        d1 = r1.json() if r1.status_code == 200 else {}
        ok1 = r1.status_code == 200 and d1.get("status") == "online"
        record_check("01_health_online", ok1, f"status={r1.status_code}, service={d1.get('service')}")

        # 2. bad password -> 401
        r2 = client.post("/api/auth/login", json={"email": "admin@mailengine.local", "password": "wrongpassword"})
        ok2 = r2.status_code == 401
        record_check("02_bad_password_401", ok2, f"status={r2.status_code}")

        # 3. good password -> token returned
        r3 = client.post("/api/auth/login", json={"email": "admin@mailengine.local", "password": "mailengine123"})
        d3 = r3.json() if r3.status_code == 200 else {}
        token = d3.get("token", "")
        ok3 = r3.status_code == 200 and bool(token) and token.startswith("sess_")
        record_check("03_good_password_token", ok3, f"token_prefix={token[:10] if token else 'none'}")

        auth_headers = {"Authorization": f"Bearer {token}"} if token else {}

        # 4. unauthenticated GET /api/campaigns -> 401
        # A fresh client with no cookie jar: the logged-in client would replay its session cookie.
        r4 = httpx.get(f"{BASE_URL}/api/campaigns", timeout=15)
        ALL_STATUS_CODES.append(r4.status_code)
        ok4 = r4.status_code == 401
        record_check("04_unauthenticated_campaigns_401", ok4, f"status={r4.status_code}")

        # 5. GET /api/campaigns with token 200 and seeded items > 0
        r5 = client.get("/api/campaigns", headers=auth_headers)
        d5 = r5.json() if r5.status_code == 200 else {}
        items5 = d5.get("items", [])
        ok5 = r5.status_code == 200 and isinstance(items5, list) and len(items5) > 0 and d5.get("total", 0) > 0
        record_check("05_campaigns_seeded_items", ok5, f"total={d5.get('total')}, items_len={len(items5)}")

        # 6. POST /api/campaigns creates a campaign (201) and GET /api/campaigns/{id} returns it
        camp_name = f"Test Campaign {secrets.token_hex(4)}"
        r6_post = client.post(
            "/api/campaigns",
            headers=auth_headers,
            json={"name": camp_name, "subject": "Hello {{first_name}}", "body": "Welcome {{first_name}} at {{company}}!"},
        )
        d6_post = r6_post.json() if r6_post.status_code == 201 else {}
        camp_id = d6_post.get("campaign", {}).get("id")
        r6_get = client.get(f"/api/campaigns/{camp_id}", headers=auth_headers) if camp_id else r6_post
        d6_get = r6_get.json() if r6_get.status_code == 200 else {}
        ok6 = (
            r6_post.status_code == 201
            and r6_get.status_code == 200
            and d6_get.get("campaign", {}).get("id") == camp_id
            and d6_get.get("campaign", {}).get("name") == camp_name
        )
        record_check("06_create_and_get_campaign", ok6, f"post_status={r6_post.status_code}, id={camp_id}")

        # 7. PATCH /api/campaigns/{id} changes name and re-GET shows the change persisted
        new_name = f"Renamed Campaign {secrets.token_hex(4)}"
        r7_patch = client.patch(f"/api/campaigns/{camp_id}", headers=auth_headers, json={"name": new_name})
        r7_get = client.get(f"/api/campaigns/{camp_id}", headers=auth_headers)
        d7_get = r7_get.json() if r7_get.status_code == 200 else {}
        ok7 = (
            r7_patch.status_code == 200
            and r7_get.status_code == 200
            and d7_get.get("campaign", {}).get("name") == new_name
        )
        record_check("07_patch_campaign_persisted", ok7, f"patch_status={r7_patch.status_code}, name={new_name}")

        # 8. DELETE /api/campaigns/{id} then GET -> 404
        r8_create = client.post(
            "/api/campaigns",
            headers=auth_headers,
            json={"name": "To Delete Campaign", "subject": "Subj", "body": "Body"},
        )
        del_camp_id = r8_create.json().get("campaign", {}).get("id", "")
        r8_del = client.delete(f"/api/campaigns/{del_camp_id}", headers=auth_headers)
        r8_get = client.get(f"/api/campaigns/{del_camp_id}", headers=auth_headers)
        ok8 = r8_del.status_code == 200 and r8_get.status_code == 404
        record_check("08_delete_campaign_404", ok8, f"del_status={r8_del.status_code}, get_status={r8_get.status_code}")

        # 9. POST /api/leads creates a lead and it appears in GET /api/leads?q=
        lead_email = f"lead_{int(time.time())}_{secrets.token_hex(4)}@example.com"
        r9_post = client.post(
            "/api/leads",
            headers=auth_headers,
            json={
                "email": lead_email,
                "firstName": "John",
                "lastName": "Doe",
                "company": "Acme Corp",
                "title": "Data Architect",
            },
        )
        d9_post = r9_post.json() if r9_post.status_code == 201 else {}
        r9_get = client.get(f"/api/leads?q={lead_email}", headers=auth_headers)
        d9_get = r9_get.json() if r9_get.status_code == 200 else {}
        leads_q = d9_get.get("items", [])
        ok9 = (
            r9_post.status_code == 201
            and r9_get.status_code == 200
            and any(l.get("email") == lead_email for l in leads_q)
        )
        record_check("09_create_lead_and_search_q", ok9, f"post_status={r9_post.status_code}, found={len(leads_q)}")

        # 10. POST /api/leads/import with a 2-row CSV imports 2 and rejects a duplicate email
        imp_e1 = f"imp1_{secrets.token_hex(4)}@example.com"
        imp_e2 = f"imp2_{secrets.token_hex(4)}@example.com"
        csv_rows = f"email,first_name,last_name,company,title\n{imp_e1},Alice,Smith,AlphaCo,CTO\n{imp_e2},Bob,Jones,BetaCo,Lead"
        r10_imp1 = client.post("/api/leads/import", headers=auth_headers, json={"csv": csv_rows})
        d10_imp1 = r10_imp1.json() if r10_imp1.status_code == 200 else {}
        ok10_first = r10_imp1.status_code == 200 and d10_imp1.get("imported") == 2

        # Re-import same CSV containing duplicate emails
        r10_imp2 = client.post("/api/leads/import", headers=auth_headers, json={"csv": csv_rows})
        d10_imp2 = r10_imp2.json() if r10_imp2.status_code == 200 else {}
        ok10_dup = (
            r10_imp2.status_code == 200
            and (d10_imp2.get("skipped", 0) >= 1 or d10_imp2.get("imported", 0) == 0 or len(d10_imp2.get("errors", [])) >= 1)
        )
        ok10 = ok10_first and ok10_dup
        record_check(
            "10_import_leads_csv_and_reject_duplicate",
            ok10,
            f"imp1={d10_imp1.get('imported')}, dup_skipped={d10_imp2.get('skipped')}",
        )

        # 11. POST /api/campaigns/{id}/steps adds a step and GET steps shows it
        step_payload = {
            "stepOrder": 1,
            "delayDays": 0,
            "subject": "Step 1: Introduction to {{company}}",
            "body": "Hi {{first_name}},\n\nWanted to introduce our team.\n\nBest,\n{{sender_name}}",
        }
        r11_post = client.post(f"/api/campaigns/{camp_id}/steps", headers=auth_headers, json=step_payload)
        d11_post = r11_post.json() if r11_post.status_code == 201 else {}
        step_id = d11_post.get("step", {}).get("id")
        r11_get = client.get(f"/api/campaigns/{camp_id}/steps", headers=auth_headers)
        d11_get = r11_get.json() if r11_get.status_code == 200 else {}
        steps_items = d11_get.get("items", [])
        ok11 = (
            r11_post.status_code == 201
            and r11_get.status_code == 200
            and bool(step_id)
            and any(s.get("id") == step_id for s in steps_items)
        )
        record_check("11_add_campaign_step_and_get", ok11, f"step_id={step_id}, total_steps={len(steps_items)}")

        # 12. end-to-end flow: create campaign + step, add lead, enroll {"all":true}, POST send-next -> sent >= 1,
        #     then GET /api/sends?campaignId= shows the send row and the enrollment current_step advanced
        # Give this check its own mailbox with headroom. The shared mailbox accumulates
        # sent_today across runs, and send-next correctly refuses to send once that hits the
        # daily limit, so reusing it made this check a function of run history.
        d12_mb = client.post(
            "/api/mailboxes",
            headers=auth_headers,
            json={"email": f"e2e_sender_{secrets.token_hex(4)}@example.com",
                  "displayName": "E2E Sender", "dailyLimit": 100000},
        ).json().get("mailbox", {})
        e2e_mailbox_id = d12_mb.get("id", "")

        e2e_c = client.post(
            "/api/campaigns",
            headers=auth_headers,
            json={"name": f"E2E Campaign {secrets.token_hex(4)}", "subject": "E2E Subj {{company}}",
                  "body": "E2E Body", "dailyLimit": 100000,
                  "fromMailboxId": e2e_mailbox_id},
        ).json().get("campaign", {})
        e2e_cid = e2e_c.get("id", "")

        client.post(
            f"/api/campaigns/{e2e_cid}/steps",
            headers=auth_headers,
            json={"stepOrder": 1, "delayDays": 0, "subject": "E2E Step 1", "body": "Hello {{first_name}}"},
        )

        e2e_lead_email = f"e2e_{secrets.token_hex(4)}@example.com"
        d12_lead = client.post(
            "/api/leads",
            headers=auth_headers,
            json={"email": e2e_lead_email, "firstName": "E2EUser", "company": "E2ECorp"},
        ).json().get("lead", {})
        e2e_lead_id = d12_lead.get("id", "")

        # Activate campaign
        client.post(f"/api/campaigns/{e2e_cid}/activate", headers=auth_headers)

        # Enrol only this check's own lead. Enrolling {"all": true} pulled in every lead the
        # workspace has accumulated, which made this check a function of run history rather
        # than of the engine: once the mailbox daily limit was spent by earlier runs, sent
        # dropped to 0 and the check rotted into a permanent failure.
        r12_enr = client.post(f"/api/campaigns/{e2e_cid}/enroll", headers=auth_headers,
                              json={"leadIds": [e2e_lead_id]})
        d12_enr = r12_enr.json() if r12_enr.status_code == 200 else {}

        # POST send-next
        r12_send = client.post(f"/api/campaigns/{e2e_cid}/send-next", headers=auth_headers, json={"limit": 25})
        d12_send = r12_send.json() if r12_send.status_code == 200 else {}
        sent_count = d12_send.get("sent", 0)

        # GET /api/sends?campaignId=
        r12_sends = client.get(f"/api/sends?campaignId={e2e_cid}", headers=auth_headers)
        d12_sends = r12_sends.json() if r12_sends.status_code == 200 else {}
        sends_list = d12_sends.get("items", [])

        # GET enrollments to verify current_step advanced
        r12_enrs = client.get(f"/api/campaigns/{e2e_cid}/enrollments", headers=auth_headers)
        d12_enrs = r12_enrs.json() if r12_enrs.status_code == 200 else {}
        enrs_list = d12_enrs.get("items", [])
        step_advanced = any(
            e.get("currentStep", e.get("current_step", 0)) >= 1 for e in enrs_list
        )

        ok12 = (
            r12_send.status_code == 200
            and sent_count >= 1
            and len(sends_list) >= 1
            and step_advanced
        )
        record_check(
            "12_e2e_enroll_and_send_next",
            ok12,
            f"enrolled={d12_enr.get('enrolled')}, sent={sent_count}, sends_rows={len(sends_list)}, advanced={step_advanced}",
        )

        # Save tracking id for Check 14
        e2e_tracking_id = sends_list[0].get("trackingId", sends_list[0].get("tracking_id", "")) if sends_list else ""

        # 13. blocked path: enroll first, THEN suppress, then send-next -> that lead is blocked
        # (blocked >= 1) and no send row is written for it.
        blocked_email = f"suppressed_{secrets.token_hex(4)}@example.com"

        b_c = client.post(
            "/api/campaigns",
            headers=auth_headers,
            json={"name": f"Blocked Flow {secrets.token_hex(4)}", "subject": "Subj", "body": "Body"},
        ).json().get("campaign", {})
        b_cid = b_c.get("id", "")

        client.post(
            f"/api/campaigns/{b_cid}/steps",
            headers=auth_headers,
            json={"stepOrder": 1, "delayDays": 0, "subject": "BStep", "body": "BBody"},
        )

        b_lead = client.post(
            "/api/leads",
            headers=auth_headers,
            json={"email": blocked_email, "firstName": "BlockedUser", "company": "BlockCo"},
        ).json().get("lead", {})
        b_lid = b_lead.get("id", "")

        client.post(f"/api/campaigns/{b_cid}/activate", headers=auth_headers)
        r13_enr = client.post(f"/api/campaigns/{b_cid}/enroll", headers=auth_headers, json={"leadIds": [b_lid]})
        enrolled_before_suppression = r13_enr.json().get("enrolled", 0)

        # Suppress only after the lead is enrolled, otherwise enroll() filters it out and
        # the send-time block path is never exercised.
        r13_sup = client.post("/api/suppressions", headers=auth_headers, json={"email": blocked_email, "reason": "manual"})

        r13_send = client.post(f"/api/campaigns/{b_cid}/send-next", headers=auth_headers, json={"limit": 25})
        d13_send = r13_send.json() if r13_send.status_code == 200 else {}
        blocked_count = d13_send.get("blocked", 0)

        # Check sends row for this campaign
        r13_sends = client.get(f"/api/sends?campaignId={b_cid}", headers=auth_headers)
        d13_sends = r13_sends.json() if r13_sends.status_code == 200 else {}
        b_sends = d13_sends.get("items", [])
        no_send_row = len([s for s in b_sends if s.get("status") == "sent" and (s.get("toEmail") == blocked_email or s.get("to_email") == blocked_email)]) == 0

        # Contract says suppressed leads block and count in blocked
        ok13 = (
            r13_sup.status_code in (200, 201)
            and enrolled_before_suppression >= 1
            and r13_send.status_code == 200
            and blocked_count >= 1
            and no_send_row
        )
        record_check(
            "13_blocked_path_suppression",
            ok13,
            f"blocked={blocked_count}, enrolled_before={enrolled_before_suppression}, "
            f"sup_status={r13_sup.status_code}, no_sent_row={no_send_row}",
        )

        # 14. tracking pixel GET /api/tracking/open/{tracking_id}.gif returns 200 image/gif and the send row gets openedAt
        # If e2e_tracking_id is empty, pick one from seeded sends
        if not e2e_tracking_id:
            all_sends = client.get("/api/sends", headers=auth_headers).json().get("items", [])
            for s in all_sends:
                tid = s.get("trackingId", s.get("tracking_id"))
                if tid:
                    e2e_tracking_id = tid
                    e2e_cid = s.get("campaignId", s.get("campaign_id", ""))
                    break

        r14_pixel = client.get(f"/api/tracking/open/{e2e_tracking_id}.gif")
        content_type = r14_pixel.headers.get("content-type", "").lower()
        ok14_resp = r14_pixel.status_code == 200 and "image/gif" in content_type

        # Verify send row gets openedAt
        r14_check = client.get(f"/api/sends?campaignId={e2e_cid}", headers=auth_headers) if e2e_cid else client.get("/api/sends", headers=auth_headers)
        send_items_14 = r14_check.json().get("items", [])
        opened_send = next(
            (s for s in send_items_14 if s.get("trackingId", s.get("tracking_id")) == e2e_tracking_id),
            None,
        )
        ok14_opened = opened_send is not None and bool(opened_send.get("openedAt", opened_send.get("opened_at")))
        ok14 = ok14_resp and ok14_opened
        record_check("14_tracking_pixel_opened_at", ok14, f"gif_status={r14_pixel.status_code}, opened={ok14_opened}")

        # 15. inbox: GET /api/inbox 200, PATCH one message isRead true, POST reply -> 200 and lead status becomes replied
        r15_inbox = client.get("/api/inbox", headers=auth_headers)
        d15_inbox = r15_inbox.json() if r15_inbox.status_code == 200 else {}
        inbox_items = d15_inbox.get("items", [])
        ok15_get = r15_inbox.status_code == 200 and len(inbox_items) > 0

        msg_target = inbox_items[0] if inbox_items else {}
        msg_id = msg_target.get("id", "")
        lead_id = msg_target.get("leadId", msg_target.get("lead_id", ""))
        from_email = msg_target.get("fromEmail", msg_target.get("from_email", ""))

        r15_patch = client.patch(f"/api/inbox/{msg_id}", headers=auth_headers, json={"isRead": True})
        d15_patch = r15_patch.json() if r15_patch.status_code == 200 else {}
        patch_msg = d15_patch.get("message", {})
        ok15_patch = r15_patch.status_code == 200 and (patch_msg.get("isRead") is True or patch_msg.get("is_read") is True)

        r15_reply = client.post(f"/api/inbox/{msg_id}/reply", headers=auth_headers, json={"body": "Thanks for your response!"})
        ok15_reply = r15_reply.status_code == 200 and r15_reply.json().get("ok") is True

        # Check lead status becomes replied
        r15_lead = client.get(f"/api/leads?q={from_email}", headers=auth_headers)
        leads_matched = r15_lead.json().get("items", []) if r15_lead.status_code == 200 else []
        matched_lead = next((l for l in leads_matched if l.get("id") == lead_id or l.get("email") == from_email), None)
        ok15_lead = matched_lead is not None and matched_lead.get("status") == "replied"

        ok15 = ok15_get and ok15_patch and ok15_reply and ok15_lead
        record_check(
            "15_inbox_patch_and_reply_flow",
            ok15,
            f"inbox_items={len(inbox_items)}, patch={ok15_patch}, reply={ok15_reply}, lead_replied={ok15_lead}",
        )

        # 16. GET /api/analytics/overview 200 with totals.sent >= seeded sends and campaigns array non-empty
        r16 = client.get("/api/analytics/overview", headers=auth_headers)
        d16 = r16.json() if r16.status_code == 200 else {}
        totals16 = d16.get("totals", {})
        camps16 = d16.get("campaigns", [])
        # Seeded sends in database.py is 40
        ok16 = (
            r16.status_code == 200
            and totals16.get("sent", 0) >= 40
            and isinstance(camps16, list)
            and len(camps16) > 0
        )
        record_check("16_analytics_overview_seeded_totals", ok16, f"sent={totals16.get('sent')}, campaigns={len(camps16)}")

        # 17. GET /openapi.json route count >= 40 and /health + /api/auth/login present (drift check)
        r17 = client.get("/openapi.json")
        d17 = r17.json() if r17.status_code == 200 else {}
        paths = d17.get("paths", {})
        route_ops_count = sum(len(methods) for path, methods in paths.items() if isinstance(methods, dict))
        total_routes = max(len(paths), route_ops_count)
        ok17 = (
            r17.status_code == 200
            and total_routes >= 40
            and "/health" in paths
            and "/api/auth/login" in paths
        )
        record_check(
            "17_openapi_drift_check",
            ok17,
            f"paths_count={len(paths)}, operations_count={route_ops_count}, /health={('/health' in paths)}",
        )

        # 18. no HTTP 5xx across the whole run (collect all status codes, assert none >= 500)
        errors_500 = [code for code in ALL_STATUS_CODES if code >= 500]
        ok18 = len(errors_500) == 0 and len(ALL_STATUS_CODES) >= 18
        record_check(
            "18_no_http_5xx_across_run",
            ok18,
            f"total_requests={len(ALL_STATUS_CODES)}, errors_5xx={errors_500}",
        )

    finally:
        client.close()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"\n{passed}/{total} live checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
