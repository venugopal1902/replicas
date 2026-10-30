"""End-to-end verification for Smile Engine against a live server on :8660.

The ledger is the money. These checks recompute balances from raw entries and assert that a
failed redemption writes nothing and that idempotency actually blocks double-awards.
"""
import json
import sys
import time
import urllib.error
import urllib.request

import os
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "tools"))
from isolated_server import start_isolated  # noqa: E402

# Each run gets its own server, database and port, so these checks do not depend on how many
# times the suite has run before or on state left behind in the live demo instance.
# Set SMILE_ENGINE_LIVE_SUITE=1 to test the long-running instance instead.
BASE = start_isolated(
    service_dir=pathlib.Path(__file__).resolve().parents[1],
    db_env="SMILE_DB",
    test_port=int(os.environ.get("SMILE_TEST_PORT", "0")),
    live_port=8660,
    admin_password="smile123",
    admin_email="admin@smile.local",
)
ROWS = []
SERVER_ERRORS = []


def rec(name, ok, detail=""):
    ROWS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:58s} {detail}")


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
    suffix = str(int(time.time()))

    st, body = call("GET", "/health")
    rec("health returns 200", st == 200, f"status={st}")
    rec("health reports members and ledger entries",
        isinstance(body, dict) and body.get("members", 0) >= 3 and body.get("ledgerEntries", 0) >= 3,
        f"members={body.get('members')} entries={body.get('ledgerEntries')}")

    st, _ = call("POST", "/api/auth/login", {"email": "admin@smile.local", "password": "nope"})
    rec("wrong password rejected with 401", st == 401, f"status={st}")

    st, body = call("POST", "/api/auth/login", {"email": "admin@smile.local", "password": "smile123"})
    token = body.get("token") if isinstance(body, dict) else None
    rec("correct password returns a token", st == 200 and bool(token), f"status={st}")
    if not token:
        return finish()

    st, _ = call("GET", "/api/members")
    rec("unauthenticated members list rejected with 401", st == 401, f"status={st}")

    st, page = call("GET", "/")
    rec("console serves HTML", st == 200 and isinstance(page, str) and "Smile Engine" in page,
        f"status={st} bytes={len(page) if isinstance(page, str) else 0}")

    # fresh member per run
    st, member = call("POST", "/api/members",
                      {"email": f"ledger-{suffix}@example.com", "name": "Ledger Probe"}, token)
    mid = member.get("id") if isinstance(member, dict) else None
    rec("new member starts at zero balance", st == 200 and member.get("balance") == 0,
        f"status={st} balance={member.get('balance') if isinstance(member, dict) else '?'}")

    # earning
    st, body = call("POST", f"/api/members/{mid}/earn",
                    {"points": 300, "reason": "purchase", "idempotency_key": f"earn-{suffix}"}, token)
    rec("earning 300 raises the balance by exactly 300",
        st == 200 and body.get("balance") == 300, f"balance={body.get('balance') if isinstance(body, dict) else '?'}")

    # idempotency: same key must not double-award
    st, body = call("POST", f"/api/members/{mid}/earn",
                    {"points": 300, "reason": "purchase", "idempotency_key": f"earn-{suffix}"}, token)
    st_b, m_now = call("GET", f"/api/members/{mid}", token=token)
    rec("replayed earn with the same idempotency key does not double-award",
        body.get("duplicate") is True and m_now.get("balance") == 300,
        f"duplicate={body.get('duplicate')} balance={m_now.get('balance')}")

    # missing idempotency key is refused
    st, _ = call("POST", f"/api/members/{mid}/earn", {"points": 50, "reason": "x"}, token)
    rec("earn without an idempotency key is refused with 422", st == 422, f"status={st}")

    # redemption
    st, body = call("POST", f"/api/members/{mid}/redeem",
                    {"reward_id": "rw_latte", "idempotency_key": f"red-{suffix}"}, token)
    rec("redeeming a 150-point reward leaves 150",
        st == 200 and body.get("balance") == 150,
        f"balance={body.get('balance') if isinstance(body, dict) else '?'} points={body.get('points') if isinstance(body, dict) else '?'}")

    # over-redeem must be refused and write nothing
    st, _ = call("POST", f"/api/members/{mid}/redeem",
                 {"reward_id": "rw_hoodie", "idempotency_key": f"over-{suffix}"}, token)
    st_b, m_now = call("GET", f"/api/members/{mid}", token=token)
    rec("redemption beyond the balance is refused with 409",
        st == 409, f"status={st}")
    rec("a refused redemption writes no ledger entry",
        m_now.get("balance") == 150, f"balance={m_now.get('balance')}")

    # replay redemption
    st, body = call("POST", f"/api/members/{mid}/redeem",
                    {"reward_id": "rw_latte", "idempotency_key": f"red-{suffix}"}, token)
    rec("replayed redemption is idempotent, balance unchanged",
        isinstance(body, dict) and body.get("duplicate") is True and body.get("balance") == 150,
        f"duplicate={body.get('duplicate') if isinstance(body, dict) else '?'} balance={body.get('balance') if isinstance(body, dict) else '?'}")

    # independent recomputation from the ledger
    st, led = call("GET", f"/api/members/{mid}/ledger", token=token)
    entries = led.get("items", []) if isinstance(led, dict) else []
    recomputed = sum(e["points"] for e in entries)
    st_m, m_final = call("GET", f"/api/members/{mid}", token=token)
    rec("balance equals the sum of its ledger entries",
        m_final.get("balance") == recomputed,
        f"api={m_final.get('balance')} recomputed={recomputed} entries={len(entries)}")
    rec("ledger records both the earn and the redeem",
        sum(1 for e in entries if e["points"] > 0) >= 1 and sum(1 for e in entries if e["points"] < 0) >= 1,
        f"positive={sum(1 for e in entries if e['points'] > 0)} negative={sum(1 for e in entries if e['points'] < 0)}")

    # tiers derive from lifetime earned, not current balance
    st, m = call("GET", f"/api/members/{mid}", token=token)
    rec("tier is derived from lifetime earned, not spendable balance",
        m.get("lifetimeEarned") == 300 and m.get("tier") == "bronze" and m.get("balance") == 150,
        f"lifetime={m.get('lifetimeEarned')} balance={m.get('balance')} tier={m.get('tier')}")

    # referrals
    st, ref = call("POST", "/api/referrals",
                   {"referral_code": member.get("referralCode"),
                    "referee_email": f"friend-{suffix}@example.com",
                    "idempotency_key": f"ref-{suffix}"}, token)
    rec("referral awards both sides",
        st == 200 and ref.get("referrerBonus") == 200 and ref.get("refereeBonus") == 100,
        f"status={st} referrer={ref.get('referrerBonus') if isinstance(ref, dict) else '?'} referee={ref.get('refereeBonus') if isinstance(ref, dict) else '?'}")

    st, _ = call("POST", "/api/referrals",
                 {"referral_code": member.get("referralCode"),
                  "referee_email": f"ledger-{suffix}@example.com",
                  "idempotency_key": f"self-{suffix}"}, token)
    rec("self-referral is refused with 409", st == 409, f"status={st}")

    st, _ = call("POST", "/api/referrals",
                 {"referral_code": member.get("referralCode"),
                  "referee_email": f"friend-{suffix}@example.com",
                  "idempotency_key": f"dup-{suffix}"}, token)
    rec("referring the same email twice is refused with 409", st == 409, f"status={st}")

    # csv parity
    st, csv_text = call("GET", "/api/report.csv", token=token)
    lines = [l for l in csv_text.splitlines() if l.strip()] if isinstance(csv_text, str) else []
    st_h, health = call("GET", "/health")
    rec("CSV header plus one row per ledger entry",
        st == 200 and lines and lines[0].startswith("entry_id,email,kind,points")
        and len(lines) - 1 == health.get("ledgerEntries"),
        f"csv_rows={len(lines) - 1} ledger_entries={health.get('ledgerEntries')}")

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
