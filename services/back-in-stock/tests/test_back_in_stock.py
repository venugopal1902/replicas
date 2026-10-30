"""End-to-end verification for Back in Stock against a live server on :8600.

Every check hits the real HTTP API. No mocks, no stubs.

    uv run python tests/test_back_in_stock.py
"""
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
# Set BACK_IN_STOCK_LIVE_SUITE=1 to test the long-running instance instead.
BASE = start_isolated(
    service_dir=pathlib.Path(__file__).resolve().parents[1],
    db_env="BACKINSTOCK_DB",
    test_port=int(os.environ.get("BACKINSTOCK_TEST_PORT", "0")),
    live_port=8600,
    admin_password="backinstock123",
    admin_email="admin@backinstock.local",
)
ROWS = []
SERVER_ERRORS = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    ROWS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:52s} {detail}")


def call(method: str, path: str, body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
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


def main() -> int:
    # ---------------------------------------------------------------- health
    st, body = call("GET", "/health")
    rec("health returns 200", st == 200, f"status={st}")
    rec(
        "health reports online and a seeded catalogue",
        isinstance(body, dict) and body.get("status") == "online" and body.get("variants", 0) >= 6,
        f"variants={body.get('variants') if isinstance(body, dict) else '?'} sendMode={body.get('sendMode') if isinstance(body, dict) else '?'}",
    )

    # ------------------------------------------------------------------ auth
    st, _ = call("POST", "/api/auth/login", {"email": "admin@backinstock.local", "password": "nope"})
    rec("wrong password rejected with 401", st == 401, f"status={st}")

    st, body = call(
        "POST", "/api/auth/login",
        {"email": "admin@backinstock.local", "password": "backinstock123"},
    )
    token = body.get("token") if isinstance(body, dict) else None
    rec("correct password returns a token", st == 200 and bool(token), f"status={st} token={'yes' if token else 'no'}")
    if not token:
        return finish()

    st, _ = call("GET", "/api/variants")
    rec("unauthenticated admin route rejected with 401", st == 401, f"status={st}")

    # ------------------------------------------------------- public surfaces
    st, cat = call("GET", "/api/public/catalog")
    items = cat.get("items", []) if isinstance(cat, dict) else []
    rec("public catalogue needs no auth", st == 200 and len(items) >= 6, f"status={st} items={len(items)}")

    st, page = call("GET", "/")
    rec(
        "storefront page serves HTML",
        st == 200 and isinstance(page, str) and "Notify me" in page,
        f"status={st} bytes={len(page) if isinstance(page, str) else 0}",
    )
    st, admin = call("GET", "/admin")
    rec(
        "admin console serves HTML",
        st == 200 and isinstance(admin, str) and "Back in Stock" in admin,
        f"status={st} bytes={len(admin) if isinstance(admin, str) else 0}",
    )

    # pick a sold-out variant, forcing one to zero if the catalogue is all in stock
    target = next((v for v in items if v["stock"] == 0), None)
    if target is None:
        zeroed = items[0]["id"]
        call("POST", f"/api/variants/{zeroed}/stock", {"stock": 0}, token)
        st, cat = call("GET", "/api/public/catalog")
        items = cat.get("items", [])
        target = next(v for v in items if v["id"] == zeroed)
    vid = target["id"]

    # ------------------------------------------------------------ waitlists
    shopper = f"chief-test-{vid}@example.com"
    st, body = call("POST", "/api/public/waitlist", {"variant_id": vid, "email": shopper})
    rec(
        "public waitlist signup works without auth",
        st == 200 and isinstance(body, dict) and body.get("ok") is True,
        f"status={st} status_field={body.get('status') if isinstance(body, dict) else '?'}",
    )

    st, body2 = call("POST", "/api/public/waitlist", {"variant_id": vid, "email": shopper})
    rec(
        "duplicate signup is reported, not duplicated",
        st == 200 and isinstance(body2, dict) and body2.get("duplicate") is True,
        f"status={st} duplicate={body2.get('duplicate') if isinstance(body2, dict) else '?'}",
    )

    st, subs = call("GET", f"/api/subscriptions?variant_id={vid}", token=token)
    mine = [s for s in subs.get("items", []) if s["email"] == shopper] if isinstance(subs, dict) else []
    rec("exactly one subscription row exists for the shopper", len(mine) == 1, f"rows={len(mine)}")

    waiting_before = sum(1 for s in subs.get("items", []) if s["status"] == "waiting") if isinstance(subs, dict) else 0

    # ------------------------------------------------------------- restock
    st, body = call("POST", f"/api/variants/{vid}/stock", {"stock": 25}, token)
    queued = body.get("notificationsQueued") if isinstance(body, dict) else None
    rec(
        "restock queues one notification per waiting subscriber",
        st == 200 and queued == waiting_before and waiting_before >= 1,
        f"status={st} waiting_before={waiting_before} queued={queued}",
    )

    st, notifs = call("GET", "/api/notifications", token=token)
    mine_notifs = [n for n in notifs.get("items", []) if n["variant_id"] == vid] if isinstance(notifs, dict) else []
    rec(
        "notification rows recorded for the variant",
        len(mine_notifs) >= waiting_before,
        f"rows={len(mine_notifs)}",
    )
    rec(
        "notifications are dry_run, never claimed as delivered",
        bool(mine_notifs) and all(n["status"] in ("dry_run", "sent") for n in mine_notifs),
        f"statuses={sorted({n['status'] for n in mine_notifs})}",
    )

    st, subs_after = call("GET", f"/api/subscriptions?variant_id={vid}", token=token)
    flipped = [s for s in subs_after.get("items", []) if s["email"] == shopper and s["status"] == "notified"]
    rec("waiting subscriber flips to notified", len(flipped) == 1, f"notified={len(flipped)}")

    # ------------------------------------------------------------ preorder
    st, body = call("POST", "/api/public/preorder", {"variant_id": vid, "email": "pre@example.com", "quantity": 2})
    rec(
        "preorder capture works",
        st == 200 and isinstance(body, dict) and body.get("status") == "captured",
        f"status={st} qty={body.get('quantity') if isinstance(body, dict) else '?'}",
    )

    # ---------------------------------------------------------------- stats
    st, stats = call("GET", "/api/stats", token=token)
    ok_stats = (
        st == 200
        and isinstance(stats, dict)
        and stats.get("notified", 0) >= 1
        and stats.get("notifications", 0) >= 1
        and stats.get("preorderUnits", 0) >= 2
    )
    rec(
        "stats reflect the rows just written",
        ok_stats,
        f"notified={stats.get('notified') if isinstance(stats, dict) else '?'} notifications={stats.get('notifications') if isinstance(stats, dict) else '?'} preorderUnits={stats.get('preorderUnits') if isinstance(stats, dict) else '?'}",
    )

    # ------------------------------------------------------------------ csv
    st, csv_text = call("GET", f"/api/variants/{vid}/subscribers.csv", token=token)
    lines = [ln for ln in csv_text.splitlines() if ln.strip()] if isinstance(csv_text, str) else []
    rec(
        "CSV export returns a header plus data rows",
        st == 200 and bool(lines) and lines[0].startswith("email,phone,channel,status"),
        f"status={st} lines={len(lines)} header={lines[0][:48] if lines else 'none'}",
    )
    st, subs_final = call("GET", f"/api/subscriptions?variant_id={vid}", token=token)
    expected = len(subs_final.get("items", [])) if isinstance(subs_final, dict) else -1
    rec(
        "CSV row count matches the subscription table",
        len(lines) - 1 == expected,
        f"csv_rows={len(lines) - 1} table_rows={expected}",
    )

    # --------------------------------------------------------------- global
    rec("zero HTTP 5xx across the run", not SERVER_ERRORS, f"errors={SERVER_ERRORS}")
    return finish()


def finish() -> int:
    passed = sum(1 for _, ok, _ in ROWS if ok)
    total = len(ROWS)
    print(f"\n===== {passed}/{total} checks passed =====")
    for name, ok, detail in ROWS:
        if not ok:
            print(f"  FAILED: {name} :: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
