"""Baremetrics replica: SaaS metrics from subscription rows.

Clean-room replacement for baremetrics.com. Computes MRR, ARR, ARPU, churn and MRR movement
from subscription records instead of estimating them, and exposes June-style "as of date"
numbers plus a daily MRR series.

Ingest is Stripe-shaped so a real webhook can point here, and a local simulator exists so the
whole thing is exercisable without live credentials.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("BAREMETRICS_DB", BASE_DIR / "data" / "baremetrics.db"))
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@baremetrics.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Metrics Admin")
PASSWORD_ROUNDS = 240_000

app = FastAPI(title="Baremetrics Engine", version="0.1.0")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    plan TEXT NOT NULL,
    mrr_cents INTEGER NOT NULL,
    status TEXT NOT NULL,           -- active | canceled
    started_at TEXT NOT NULL,
    canceled_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    subscription_id TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, _ = stored.split("$", 2)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), stored)


PLANS = [("starter", 2900), ("growth", 4900), ("scale", 9900)]


def seed(conn: sqlite3.Connection) -> None:
    import random

    rng = random.Random(20261030)
    base = datetime.now(timezone.utc)
    for i in range(60):
        cid = f"cus_{i:03d}"
        started = base - timedelta(days=rng.randint(1, 120))
        conn.execute(
            "INSERT INTO customers (id, email, name, created_at) VALUES (?,?,?,?)",
            (cid, f"user{i:03d}@example.com", f"Customer {i:03d}", started.isoformat(timespec="seconds")),
        )
        plan, mrr = PLANS[i % len(PLANS)]
        # every 6th subscriber churned, half of those within the last 30 days
        if i % 6 == 0:
            churn_days_ago = rng.randint(1, 30) if i % 12 == 0 else rng.randint(31, 90)
            canceled = base - timedelta(days=churn_days_ago)
            conn.execute(
                "INSERT INTO subscriptions (id, customer_id, plan, mrr_cents, status, started_at, canceled_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (f"sub_{i:03d}", cid, plan, mrr, "canceled",
                 started.isoformat(timespec="seconds"), canceled.isoformat(timespec="seconds")),
            )
        else:
            conn.execute(
                "INSERT INTO subscriptions (id, customer_id, plan, mrr_cents, status, started_at, canceled_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (f"sub_{i:03d}", cid, plan, mrr, "active", started.isoformat(timespec="seconds"), None),
            )
    conn.commit()


def init_db() -> None:
    with closing(connect()) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
        if conn.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone():
            return
        if not ADMIN_PASSWORD:
            raise RuntimeError(
                "No admin account exists and ADMIN_PASSWORD is not set.\n"
                "Set it before the first start, for example:\n"
                "  export ADMIN_PASSWORD='choose-a-long-unique-password'\n"
                "No default password ships with this service."
            )
        conn.execute(
            "INSERT INTO users (email, name, password_hash, created_at) VALUES (?,?,?,?)",
            (ADMIN_EMAIL, ADMIN_NAME, hash_password(ADMIN_PASSWORD), now()),
        )
        conn.commit()
        seed(conn)


@app.on_event("startup")
def _startup() -> None:
    init_db()


def current_user(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT s.expires_at, u.id, u.email FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (token,),
        ).fetchone()
    if not row or row["expires_at"] < now():
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return {"id": row["id"], "email": row["email"]}


# ------------------------------------------------------------------- metrics core
def mrr_at(conn: sqlite3.Connection, as_of: str) -> int:
    """MRR as of a date: started on/before it, not canceled on or before it."""
    row = conn.execute(
        "SELECT COALESCE(SUM(mrr_cents),0) s FROM subscriptions"
        " WHERE substr(started_at,1,10) <= ?"
        " AND (canceled_at IS NULL OR substr(canceled_at,1,10) > ?)",
        (as_of, as_of),
    ).fetchone()
    return int(row["s"])


def active_at(conn: sqlite3.Connection, as_of: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) c FROM subscriptions"
        " WHERE substr(started_at,1,10) <= ?"
        " AND (canceled_at IS NULL OR substr(canceled_at,1,10) > ?)",
        (as_of, as_of),
    ).fetchone()
    return int(row["c"])


def summary(conn: sqlite3.Connection, as_of: str) -> Dict[str, Any]:
    mrr = mrr_at(conn, as_of)
    active = active_at(conn, as_of)
    period_start = (datetime.fromisoformat(as_of) - timedelta(days=30)).date().isoformat()

    starting_mrr = mrr_at(conn, period_start)
    new_mrr = int(conn.execute(
        "SELECT COALESCE(SUM(mrr_cents),0) s FROM subscriptions WHERE substr(started_at,1,10) > ?"
        " AND substr(started_at,1,10) <= ?", (period_start, as_of)).fetchone()["s"])
    churned_mrr = int(conn.execute(
        "SELECT COALESCE(SUM(mrr_cents),0) s FROM subscriptions WHERE canceled_at IS NOT NULL"
        " AND substr(canceled_at,1,10) > ? AND substr(canceled_at,1,10) <= ?",
        (period_start, as_of)).fetchone()["s"])
    new_subs = int(conn.execute(
        "SELECT COUNT(*) c FROM subscriptions WHERE substr(started_at,1,10) > ? AND substr(started_at,1,10) <= ?",
        (period_start, as_of)).fetchone()["c"])
    churned_subs = int(conn.execute(
        "SELECT COUNT(*) c FROM subscriptions WHERE canceled_at IS NOT NULL"
        " AND substr(canceled_at,1,10) > ? AND substr(canceled_at,1,10) <= ?",
        (period_start, as_of)).fetchone()["c"])

    churn_rate = round((churned_mrr / starting_mrr) * 100, 2) if starting_mrr else 0.0
    arpu = round(mrr / active) if active else 0
    return {
        "asOf": as_of,
        "mrrCents": mrr,
        "arrCents": mrr * 12,
        "activeSubscriptions": active,
        "arpuCents": arpu,
        "period": {"from": period_start, "to": as_of, "days": 30},
        "newMrrCents": new_mrr,
        "churnedMrrCents": churned_mrr,
        "netNewMrrCents": new_mrr - churned_mrr,
        "newSubscriptions": new_subs,
        "churnedSubscriptions": churned_subs,
        "startingMrrCents": starting_mrr,
        "churnRatePct": churn_rate,
        "quickRatio": round(new_mrr / churned_mrr, 2) if churned_mrr else None,
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    with closing(connect()) as conn:
        subs = conn.execute("SELECT COUNT(*) c FROM subscriptions").fetchone()["c"]
        mrr = mrr_at(conn, today())
    return {"status": "online", "service": "Baremetrics Engine", "version": "0.1.0",
            "subscriptions": subs, "mrrCents": mrr, "asOf": today()}


@app.post("/api/auth/login")
def login(body: Dict[str, str] = Body(...)) -> Dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    with closing(connect()) as conn:
        user = conn.execute("SELECT * FROM users WHERE lower(email)=?", (email,)).fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Wrong email or password")
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(timespec="seconds")
        conn.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
                     (token, user["id"], expires))
        conn.commit()
    return {"token": token, "expiresAt": expires}


@app.get("/api/metrics/summary")
def metrics_summary(as_of: Optional[str] = None, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    day = as_of or today()
    try:
        datetime.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=422, detail="as_of must be YYYY-MM-DD")
    with closing(connect()) as conn:
        return summary(conn, day)


@app.get("/api/metrics/mrr-series")
def mrr_series(days: int = 30, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    if not 1 <= days <= 365:
        raise HTTPException(status_code=422, detail="days must be 1..365")
    end = datetime.now(timezone.utc).date()
    points: List[Dict[str, Any]] = []
    with closing(connect()) as conn:
        for offset in range(days - 1, -1, -1):
            d = (end - timedelta(days=offset)).isoformat()
            points.append({"date": d, "mrrCents": mrr_at(conn, d), "active": active_at(conn, d)})
    return {"points": points, "days": days}


@app.get("/api/customers")
def customers(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT c.id, c.email, c.name, c.created_at, s.id sub_id, s.plan, s.mrr_cents, s.status,"
            " s.started_at, s.canceled_at FROM customers c"
            " LEFT JOIN subscriptions s ON s.customer_id = c.id ORDER BY c.id"
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@app.get("/api/subscriptions")
def subscriptions(status: Optional[str] = None, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    q = "SELECT * FROM subscriptions"
    args: List[Any] = []
    if status:
        q += " WHERE status = ?"
        args.append(status)
    q += " ORDER BY id"
    with closing(connect()) as conn:
        rows = conn.execute(q, args).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@app.post("/api/subscriptions")
def create_subscription(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    plan = body.get("plan") or "starter"
    mrr = body.get("mrr_cents")
    if not email:
        raise HTTPException(status_code=422, detail="email is required")
    if mrr is None:
        mrr = dict(PLANS).get(plan)
    if not isinstance(mrr, int) or mrr < 0:
        raise HTTPException(status_code=422, detail="mrr_cents must be a non-negative integer")
    with closing(connect()) as conn:
        cust = conn.execute("SELECT * FROM customers WHERE email=?", (email,)).fetchone()
        if not cust:
            cid = "cus_" + secrets.token_hex(6)
            conn.execute("INSERT INTO customers (id, email, name, created_at) VALUES (?,?,?,?)",
                         (cid, email, body.get("name") or email.split("@")[0].title(), now()))
        else:
            cid = cust["id"]
        sid = "sub_" + secrets.token_hex(6)
        conn.execute(
            "INSERT INTO subscriptions (id, customer_id, plan, mrr_cents, status, started_at, canceled_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (sid, cid, plan, mrr, "active", body.get("started_at") or now(), None),
        )
        conn.execute("INSERT INTO events (kind, subscription_id, detail, created_at) VALUES (?,?,?,?)",
                     ("subscription_created", sid, plan, now()))
        conn.commit()
    return {"ok": True, "subscriptionId": sid, "customerId": cid, "mrrCents": mrr, "plan": plan}


@app.post("/api/subscriptions/{sub_id}/cancel")
def cancel_subscription(sub_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        if not sub:
            raise HTTPException(status_code=404, detail="Subscription not found")
        if sub["status"] == "canceled":
            return {"ok": True, "alreadyCanceled": True, "subscriptionId": sub_id}
        conn.execute("UPDATE subscriptions SET status='canceled', canceled_at=? WHERE id=?", (now(), sub_id))
        conn.execute("INSERT INTO events (kind, subscription_id, detail, created_at) VALUES (?,?,?,?)",
                     ("subscription_canceled", sub_id, sub["plan"], now()))
        conn.commit()
    return {"ok": True, "alreadyCanceled": False, "subscriptionId": sub_id, "canceledAt": now()}


@app.get("/api/report.csv", response_class=PlainTextResponse)
def report_csv(user: Dict[str, Any] = Depends(current_user)) -> PlainTextResponse:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT s.id, c.email, s.plan, s.mrr_cents, s.status, s.started_at,"
            " COALESCE(s.canceled_at,'') AS canceled_at"
            " FROM subscriptions s JOIN customers c ON c.id=s.customer_id ORDER BY s.id"
        ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["subscription_id", "email", "plan", "mrr_cents", "status", "started_at", "canceled_at"])
    for r in rows:
        w.writerow([r["id"], r["email"], r["plan"], r["mrr_cents"], r["status"], r["started_at"], r["canceled_at"]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="subscriptions.csv"'})


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request) -> Dict[str, Any]:
    """Stripe-shaped intake. Not signature verified here: this is ingest, not a payment action."""
    try:
        payload = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    event = payload.get("type") or ""
    obj = (payload.get("data") or {}).get("object") or {}
    customer_email = obj.get("customer_email") or (obj.get("customer") or {}).get("email") or ""
    amount = int(obj.get("amount") or obj.get("mrr_cents") or 0)
    with closing(connect()) as conn:
        if event in ("customer.subscription.created", "invoice.payment_succeeded"):
            if not customer_email:
                return {"received": True, "action": "ignored", "reason": "no customer email"}
            r = create_subscription({"email": customer_email, "mrr_cents": amount or None,
                                     "plan": obj.get("plan") or "starter"}, {"id": 0, "email": "webhook"})
            return {"received": True, "action": "subscription_created", **r}
        if event in ("customer.subscription.deleted", "customer.subscription.canceled"):
            sub_id = obj.get("id") or ""
            row = conn.execute("SELECT id FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
            if not row:
                return {"received": True, "action": "ignored", "reason": "unknown subscription"}
            conn.execute("UPDATE subscriptions SET status='canceled', canceled_at=? WHERE id=?", (now(), sub_id))
            conn.execute("INSERT INTO events (kind, subscription_id, detail, created_at) VALUES (?,?,?,?)",
                         ("subscription_canceled", sub_id, "stripe", now()))
            conn.commit()
            return {"received": True, "action": "subscription_canceled", "subscriptionId": sub_id}
        return {"received": True, "action": "ignored", "type": event}


ADMIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Baremetrics Engine</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{background:#090b10;color:#e6e9ef;font:14px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0}
header{border-bottom:1px solid #1d2330;padding:14px 22px;display:flex;justify-content:space-between}
h1{font-size:16px;margin:0}.muted{color:#8b93a3}
main{padding:22px;max-width:1100px;margin:0 auto}
input{background:#0b0d12;border:1px solid #2a3243;color:#e6e9ef;border-radius:6px;padding:7px 9px;margin-right:6px}
button{background:#2f6feb;border:0;color:#fff;border-radius:6px;padding:7px 13px;cursor:pointer;font-weight:600}
.grid{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}
.stat{border:1px solid #1d2330;border-radius:8px;padding:10px 14px;background:#0e1117;min-width:130px}
.stat b{display:block;font-size:20px}.stat span{color:#8b93a3;font-size:12px}
table{width:100%;border-collapse:collapse;margin-top:16px;font-size:13px}
th{text-align:left;color:#8b93a3;border-bottom:1px solid #1d2330;padding:8px 6px}
td{border-bottom:1px solid #141922;padding:8px 6px}
.err{color:#ff8f8f}
</style></head><body>
<header><h1>Baremetrics Engine <span class="muted">metrics</span></h1><span class="muted" id="who"></span></header>
<main>
<div id="login"><p class="muted">Sign in with the admin account you configured at first start.</p>
<input id="email" value="admin@baremetrics.local"><input id="password" type="password" placeholder="password">
<button onclick="signin()">Sign in</button> <span id="loginmsg" class="err"></span></div>
<div id="panel" style="display:none">
  <div class="grid" id="stats"></div>
  <h3 style="font-size:14px;margin-top:22px">MRR, last 30 days</h3>
  <div id="chart" style="display:flex;align-items:flex-end;gap:2px;height:90px;margin-top:8px"></div>
</div>
</main>
<script>
let token=localStorage.getItem('bm_token')||'';
const H=()=>({'Content-Type':'application/json','Authorization':'Bearer '+token});
async function signin(){
  const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('email').value,password:document.getElementById('password').value})});
  const d=await r.json(); if(!r.ok){document.getElementById('loginmsg').textContent=d.detail||'failed';return;}
  token=d.token;localStorage.setItem('bm_token',token);boot();
}
async function boot(){
  if(!token)return;
  const r=await fetch('/api/metrics/summary',{headers:H()});
  if(!r.ok){document.getElementById('login').style.display='block';document.getElementById('panel').style.display='none';return;}
  document.getElementById('login').style.display='none';document.getElementById('panel').style.display='block';
  document.getElementById('who').textContent='as of '+(await r.json()).asOf;
  const s=await (await fetch('/api/metrics/summary',{headers:H()})).json();
  const money=c=>'$'+(c/100).toLocaleString(undefined,{maximumFractionDigits:0});
  document.getElementById('stats').innerHTML=[['MRR',money(s.mrrCents)],['ARR',money(s.arrCents)],['Active',s.activeSubscriptions],['ARPU',money(s.arpuCents)],['New MRR',money(s.newMrrCents)],['Churned MRR',money(s.churnedMrrCents)],['Churn %',s.churnRatePct]]
    .map(([k,v])=>`<div class="stat"><span>${k}</span><b>${v}</b></div>`).join('');
  const ser=await (await fetch('/api/metrics/mrr-series?days=30',{headers:H()})).json();
  const max=Math.max(...ser.points.map(p=>p.mrrCents),1);
  document.getElementById('chart').innerHTML=ser.points.map(p=>`<div title="${p.date}: ${money(p.mrrCents)}" style="flex:1;background:#2f6feb;height:${Math.max(4,(p.mrrCents/max)*90)}px"></div>`).join('');
}
boot();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def console() -> HTMLResponse:
    return HTMLResponse(ADMIN_PAGE)
