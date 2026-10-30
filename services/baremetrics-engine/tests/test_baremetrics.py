"""End-to-end verification for Baremetrics Engine against a live server on :8650.

The important checks recompute the headline metrics independently from the raw subscription
rows and compare them to what the API reports, so a wrong formula cannot pass.
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "http://127.0.0.1:8650"
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
    rec("health reports seeded subscriptions and positive MRR",
        isinstance(body, dict) and body.get("subscriptions", 0) >= 60 and body.get("mrrCents", 0) > 0,
        f"subs={body.get('subscriptions')} mrr={body.get('mrrCents')}")

    st, _ = call("POST", "/api/auth/login", {"email": "admin@baremetrics.local", "password": "wrong"})
    rec("wrong password rejected with 401", st == 401, f"status={st}")

    st, body = call("POST", "/api/auth/login",
                    {"email": "admin@baremetrics.local", "password": "baremetrics123"})
    token = body.get("token") if isinstance(body, dict) else None
    rec("correct password returns a token", st == 200 and bool(token), f"status={st}")
    if not token:
        return finish()

    st, _ = call("GET", "/api/metrics/summary")
    rec("unauthenticated metrics rejected with 401", st == 401, f"status={st}")

    st, page = call("GET", "/")
    rec("console serves HTML", st == 200 and isinstance(page, str) and "Baremetrics" in page,
        f"status={st} bytes={len(page) if isinstance(page, str) else 0}")

    # ---- independent recomputation from raw rows
    st, subs = call("GET", "/api/subscriptions", token=token)
    raw = subs.get("items", []) if isinstance(subs, dict) else []
    st, m = call("GET", "/api/metrics/summary", token=token)
    as_of = m["asOf"]

    active = [s for s in raw if s["started_at"][:10] <= as_of and (s["canceled_at"] is None or s["canceled_at"][:10] > as_of)]
    expected_mrr = sum(s["mrr_cents"] for s in active)
    rec("MRR equals the sum of active subscriptions",
        m["mrrCents"] == expected_mrr,
        f"api={m['mrrCents']} recomputed={expected_mrr}")
    rec("active subscription count matches the raw rows",
        m["activeSubscriptions"] == len(active),
        f"api={m['activeSubscriptions']} recomputed={len(active)}")
    rec("ARR is exactly 12x MRR",
        m["arrCents"] == m["mrrCents"] * 12,
        f"arr={m['arrCents']} mrr*12={m['mrrCents'] * 12}")
    expected_arpu = round(expected_mrr / len(active)) if active else 0
    rec("ARPU equals MRR divided by active count",
        m["arpuCents"] == expected_arpu,
        f"api={m['arpuCents']} recomputed={expected_arpu}")

    period_start = m["period"]["from"]
    new_expected = sum(s["mrr_cents"] for s in raw if period_start < s["started_at"][:10] <= as_of)
    churned_expected = sum(s["mrr_cents"] for s in raw
                           if s["canceled_at"] and period_start < s["canceled_at"][:10] <= as_of)
    rec("new MRR matches subscriptions started in the window",
        m["newMrrCents"] == new_expected,
        f"api={m['newMrrCents']} recomputed={new_expected}")
    rec("churned MRR matches subscriptions canceled in the window",
        m["churnedMrrCents"] == churned_expected,
        f"api={m['churnedMrrCents']} recomputed={churned_expected}")
    rec("net new MRR equals new minus churned",
        m["netNewMrrCents"] == m["newMrrCents"] - m["churnedMrrCents"],
        f"net={m['netNewMrrCents']}")

    # ---- series monotonicity sanity on a stable window
    st, series = call("GET", "/api/metrics/mrr-series?days=10", token=token)
    pts = series.get("points", []) if isinstance(series, dict) else []
    rec("MRR series returns the requested number of days",
        len(pts) == 10 and pts[-1]["date"] == as_of,
        f"points={len(pts)} last={pts[-1]['date'] if pts else 'none'}")
    rec("series MRR is never negative",
        all(p["mrrCents"] >= 0 for p in pts),
        f"min={min((p['mrrCents'] for p in pts), default=None)}")

    # ---- mutation affects metrics predictably
    before = m["mrrCents"]
    st, body = call("POST", "/api/subscriptions",
                    {"email": "new-metric-probe@example.com", "plan": "scale", "mrr_cents": 9900}, token)
    new_sub = body.get("subscriptionId") if isinstance(body, dict) else None
    st2, m2 = call("GET", "/api/metrics/summary", token=token)
    rec("creating a subscription raises MRR by exactly its amount",
        st == 200 and m2["mrrCents"] == before + 9900,
        f"before={before} after={m2['mrrCents']} delta={m2['mrrCents'] - before}")

    st, body = call("POST", f"/api/subscriptions/{new_sub}/cancel", {}, token)
    st3, m3 = call("GET", "/api/metrics/summary", token=token)
    rec("cancelling it returns MRR to the previous value",
        st == 200 and m3["mrrCents"] == before,
        f"afterCancel={m3['mrrCents']} expected={before}")
    rec("cancel is idempotent, second call reports alreadyCanceled",
        isinstance(body, dict) and body.get("alreadyCanceled") is False and
        call("POST", f"/api/subscriptions/{new_sub}/cancel", {}, token)[1].get("alreadyCanceled") is True,
        "second cancel flagged as already done")

    # ---- stripe-shaped ingest
    st, body = call("POST", "/webhooks/stripe", {
        "type": "customer.subscription.created",
        "data": {"object": {"customer_email": "stripe-ingest@example.com", "amount": 4900, "plan": "growth"}},
    })
    rec("stripe webhook ingests a subscription",
        st == 200 and body.get("action") == "subscription_created",
        f"status={st} action={body.get('action') if isinstance(body, dict) else '?'}")
    st, body = call("POST", "/webhooks/stripe", {"type": "customer.invoice.unknown", "data": {"object": {}}})
    rec("unknown webhook events are ignored, not errored",
        st == 200 and body.get("action") == "ignored",
        f"status={st} action={body.get('action') if isinstance(body, dict) else '?'}")

    # ---- csv
    st, csv_text = call("GET", "/api/report.csv", token=token)
    lines = [l for l in csv_text.splitlines() if l.strip()] if isinstance(csv_text, str) else []
    st_s, subs_now = call("GET", "/api/subscriptions", token=token)
    expected_rows = len(subs_now.get("items", []))
    rec("CSV header plus one row per subscription",
        st == 200 and lines and lines[0].startswith("subscription_id,email") and len(lines) - 1 == expected_rows,
        f"csv_rows={len(lines) - 1} table_rows={expected_rows}")

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
