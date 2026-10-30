"""End-to-end verification for Churn Buster against a live server on :8610."""
import json
import sys
import urllib.error
import urllib.request

import os
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "tools"))
from isolated_server import start_isolated  # noqa: E402

# Each run gets its own server, database and port, so these checks do not depend on how many
# times the suite has run before or on state left behind in the live demo instance.
# Set CHURN_BUSTER_LIVE_SUITE=1 to test the long-running instance instead.
BASE = start_isolated(
    service_dir=pathlib.Path(__file__).resolve().parents[1],
    db_env="CHURN_DB",
    test_port=int(os.environ.get("CHURN_TEST_PORT", "0")),
    live_port=8610,
    admin_password="churnbuster123",
    admin_email="admin@churnbuster.local",
)
ROWS = []
SERVER_ERRORS = []


def rec(name, ok, detail=""):
    ROWS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:56s} {detail}")


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            if r.status >= 500:
                SERVER_ERRORS.append(f"{r.status} {path}")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        if e.code >= 500:
            SERVER_ERRORS.append(f"{e.code} {path}")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def main():
    st, body = call("GET", "/health")
    rec("health returns 200", st == 200, f"status={st}")
    rec(
        "health reports online with a default campaign",
        isinstance(body, dict) and body.get("status") == "online" and body.get("sendMode") == "dry_run",
        f"sendMode={body.get('sendMode') if isinstance(body, dict) else '?'}",
    )

    st, _ = call("POST", "/api/auth/login", {"email": "admin@churnbuster.local", "password": "bad"})
    rec("wrong password rejected with 401", st == 401, f"status={st}")

    st, body = call("POST", "/api/auth/login",
                    {"email": "admin@churnbuster.local", "password": "churnbuster123"})
    token = body.get("token") if isinstance(body, dict) else None
    rec("correct password returns a token", st == 200 and bool(token), f"status={st}")
    if not token:
        return finish()

    st, _ = call("GET", "/api/failures")
    rec("unauthenticated admin route rejected with 401", st == 401, f"status={st}")

    st, page = call("GET", "/")
    rec("admin console serves HTML", st == 200 and isinstance(page, str) and "Churn Buster" in page,
        f"status={st} bytes={len(page) if isinstance(page, str) else 0}")

    st, camps = call("GET", "/api/campaigns", token=token)
    steps = camps["items"][0]["steps"] if isinstance(camps, dict) and camps.get("items") else []
    rec("seeded campaign exposes its dunning steps", len(steps) >= 3,
        f"steps={len(steps)} delays={[s['delay_minutes'] for s in steps]}")

    # fresh customer so the run is repeatable
    import time
    email = f"chief-{int(time.time())}@example.com"
    st, body = call("POST", "/api/simulate/payment-failure",
                    {"customer_email": email, "amount_cents": 4900, "customer_name": "Chief Test", "mrr_cents": 4900},
                    token)
    failure_id = body.get("failureId") if isinstance(body, dict) else None
    rec("simulated failed payment opens a failure",
        st == 200 and isinstance(body, dict) and body.get("ok") is True and bool(failure_id),
        f"status={st} failureId={failure_id}")
    rec("failure queues one email per campaign step",
        isinstance(body, dict) and body.get("emailsQueued") == len(steps),
        f"queued={body.get('emailsQueued') if isinstance(body, dict) else '?'} steps={len(steps)}")

    st, body2 = call("POST", "/api/simulate/payment-failure",
                     {"customer_email": email, "amount_cents": 4900}, token)
    rec("second failure for the same open customer is not duplicated",
        isinstance(body2, dict) and body2.get("duplicate") is True,
        f"duplicate={body2.get('duplicate') if isinstance(body2, dict) else '?'}")

    st, emails = call("GET", "/api/emails", token=token)
    mine = [e for e in emails.get("items", []) if e["to_address"] == email] if isinstance(emails, dict) else []
    rec("emails are queued, not sent", bool(mine) and all(e["status"] == "queued" for e in mine),
        f"rows={len(mine)} statuses={sorted({e['status'] for e in mine})}")

    st, body = call("POST", "/api/dispatch", {}, token)
    rec("dispatch processes the due first-step email", st == 200 and body.get("processed", 0) >= 1,
        f"processed={body.get('processed') if isinstance(body, dict) else '?'}")

    st, emails = call("GET", "/api/emails", token=token)
    mine = [e for e in emails.get("items", []) if e["to_address"] == email]
    due_row = [e for e in mine if e["status"] == "dry_run"]
    later = [e for e in mine if e["status"] == "queued"]
    rec("first step is dry_run, later steps stay queued",
        bool(due_row) and bool(later),
        f"dry_run={len(due_row)} still_queued={len(later)}")

    st, body = call("POST", f"/api/failures/{failure_id}/recover", {}, token)
    rec("recovery succeeds and cancels pending emails",
        st == 200 and body.get("alreadyRecovered") is False and body.get("emailsCancelled", 0) >= 1,
        f"cancelled={body.get('emailsCancelled') if isinstance(body, dict) else '?'}")

    st, emails = call("GET", "/api/emails", token=token)
    mine = [e for e in emails.get("items", []) if e["to_address"] == email]
    rec("no email for a recovered failure is left queued",
        all(e["status"] != "queued" for e in mine),
        f"statuses={sorted({e['status'] for e in mine})}")

    # Stripe webhook path, round trip
    hook_email = f"hook-{int(time.time())}@example.com"
    st, body = call("POST", "/webhooks/stripe", {
        "type": "invoice.payment_failed",
        "data": {"object": {"customer_email": hook_email, "amount_due": 9900}},
    })
    rec("stripe webhook opens a failure",
        st == 200 and body.get("action") == "failure_opened",
        f"status={st} action={body.get('action') if isinstance(body, dict) else '?'}")

    st, body = call("POST", "/webhooks/stripe", {
        "type": "invoice.paid",
        "data": {"object": {"customer_email": hook_email, "amount": 9900}},
    })
    rec("stripe webhook payment recovers the failure",
        st == 200 and body.get("action") == "recovered",
        f"status={st} action={body.get('action') if isinstance(body, dict) else '?'}")

    st, body = call("POST", "/webhooks/stripe", {"type": "customer.created", "data": {"object": {}}})
    rec("unrelated webhook events are ignored, not errored",
        st == 200 and body.get("action") == "ignored",
        f"status={st} action={body.get('action') if isinstance(body, dict) else '?'}")

    st, a = call("GET", "/api/analytics", token=token)
    st_f, fails = call("GET", "/api/failures", token=token)
    st_r, recovered = call("GET", "/api/failures?status=recovered", token=token)
    ok_counts = (
        st == 200 and isinstance(a, dict)
        and a["failures"] == len(fails.get("items", []))
        and a["recovered"] == len(recovered.get("items", []))
        and 0 <= a["recoveryRatePct"] <= 100
    )
    rec("analytics counts match the failure table",
        ok_counts,
        f"failures={a.get('failures')} rows={len(fails.get('items', []))} recovered={a.get('recovered')} rate={a.get('recoveryRatePct')}%")

    st, csv_text = call("GET", "/api/report.csv", token=token)
    lines = [l for l in csv_text.splitlines() if l.strip()] if isinstance(csv_text, str) else []
    rec("CSV report returns a header plus rows",
        st == 200 and lines and lines[0].startswith("failure_id,email"),
        f"status={st} lines={len(lines)}")

    rec("zero HTTP 5xx across the run", not SERVER_ERRORS, f"errors={SERVER_ERRORS}")
    return finish()


def finish():
    passed = sum(1 for _, ok, _ in ROWS if ok)
    total = len(ROWS)
    print(f"\n===== {passed}/{total} checks passed =====")
    for name, ok, detail in ROWS:
        if not ok:
            print(f"  FAILED: {name} :: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
