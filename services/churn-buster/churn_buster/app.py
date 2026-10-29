"""Churn Buster replica: failed-payment dunning, cancellation flow and recovery analytics.

Clean-room replacement for churnbuster.io, which starts at $269/month.
Billing-provider connection is modelled: the Stripe webhook shape is accepted, and a
simulator exists so the whole recovery loop can be exercised without live credentials.

Emails are queued and dispatched as dry_run by default. Nothing is ever reported sent
unless SMTP was configured.
"""
from __future__ import annotations

import hashlib
import hmac
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
DB_PATH = Path(os.environ.get("CHURN_DB", BASE_DIR / "data" / "churnbuster.db"))
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@churnbuster.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "churnbuster123")
SEND_MODE = os.environ.get("SEND_MODE", "dry_run")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
PASSWORD_ROUNDS = 240_000

app = FastAPI(title="Churn Buster", version="0.1.0")


# --------------------------------------------------------------------------- db
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    mrr_cents INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaign_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    delay_minutes INTEGER NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'usd',
    status TEXT NOT NULL DEFAULT 'open',
    reason TEXT,
    source TEXT NOT NULL DEFAULT 'simulator',
    campaign_id INTEGER,
    created_at TEXT NOT NULL,
    recovered_at TEXT
);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    failure_id INTEGER NOT NULL REFERENCES failures(id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL,
    step_id INTEGER,
    to_address TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    due_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    failure_id INTEGER,
    customer_id INTEGER,
    kind TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


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


DEFAULT_STEPS = [
    (0, "Your payment did not go through", "Hi {name}, we could not charge your card. Update it here to keep your account active."),
    (60 * 24, "Still having trouble with your payment", "Hi {name}, your subscription is still unpaid. A quick card update fixes it."),
    (60 * 24 * 3, "Last chance before we pause your account", "Hi {name}, we will pause your account tomorrow unless the payment goes through."),
]


def seed(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        "INSERT INTO campaigns (name, active, created_at) VALUES (?,?,?)",
        ("Recovery 1", 1, now()),
    )
    campaign_id = cur.lastrowid
    for pos, (delay, subject, body) in enumerate(DEFAULT_STEPS, start=1):
        conn.execute(
            "INSERT INTO campaign_steps (campaign_id, position, delay_minutes, subject, body)"
            " VALUES (?,?,?,?,?)",
            (campaign_id, pos, delay, subject, body),
        )
    demo = [
        ("maya@example.com", "Maya Okafor", 4900),
        ("dev@example.com", "Dev Patel", 12900),
        ("sam@example.com", "Sam Rivera", 2900),
    ]
    for email, name, mrr in demo:
        conn.execute(
            "INSERT INTO customers (email, name, mrr_cents, status, created_at) VALUES (?,?,?,?,?)",
            (email, name, mrr, "active", now()),
        )
    conn.commit()


def init_db() -> None:
    with closing(connect()) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
        if conn.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone():
            return
        conn.execute(
            "INSERT INTO users (email, name, password_hash, created_at) VALUES (?,?,?,?)",
            (ADMIN_EMAIL, "Billing Admin", hash_password(ADMIN_PASSWORD), now()),
        )
        conn.commit()
        seed(conn)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ------------------------------------------------------------------------- auth
def current_user(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT s.expires_at, u.id, u.email, u.name FROM sessions s JOIN users u ON u.id=s.user_id"
            " WHERE s.token = ?",
            (token,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid session")
    if row["expires_at"] < now():
        raise HTTPException(status_code=401, detail="Session expired")
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


# ------------------------------------------------------------------ core logic
def enroll(conn: sqlite3.Connection, failure_id: int, customer_email: str, customer_name: str) -> int:
    """Enrol a failure in the active campaign. Returns queued email count."""
    campaign = conn.execute("SELECT * FROM campaigns WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    if not campaign:
        return 0
    steps = conn.execute(
        "SELECT * FROM campaign_steps WHERE campaign_id = ? ORDER BY position", (campaign["id"],)
    ).fetchall()
    count = 0
    base = datetime.now(timezone.utc)
    for step in steps:
        due = base + timedelta(minutes=step["delay_minutes"])
        body = step["body"].replace("{name}", customer_name or customer_email)
        conn.execute(
            "INSERT INTO emails (failure_id, customer_id, step_id, to_address, subject, body, status,"
            " due_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (failure_id, conn.execute("SELECT customer_id FROM failures WHERE id=?", (failure_id,)).fetchone()["customer_id"],
             step["id"], customer_email, step["subject"], body, "queued", iso(due), now()),
        )
        count += 1
    conn.execute("UPDATE failures SET campaign_id = ? WHERE id = ?", (campaign["id"], failure_id))
    return count


def open_failure(conn: sqlite3.Connection, email: str, amount_cents: int, name: Optional[str],
                 reason: Optional[str], source: str, currency: str = "usd",
                 mrr_cents: Optional[int] = None) -> Dict[str, Any]:
    email = (email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="customer email is required")
    customer = conn.execute("SELECT * FROM customers WHERE lower(email) = ?", (email,)).fetchone()
    if not customer:
        cur = conn.execute(
            "INSERT INTO customers (email, name, mrr_cents, status, created_at) VALUES (?,?,?,?,?)",
            (email, name or email.split("@")[0].title(), mrr_cents or amount_cents, "active", now()),
        )
        customer = conn.execute("SELECT * FROM customers WHERE id = ?", (cur.lastrowid,)).fetchone()
    elif mrr_cents is not None:
        conn.execute("UPDATE customers SET mrr_cents = ? WHERE id = ?", (mrr_cents, customer["id"]))

    existing = conn.execute(
        "SELECT * FROM failures WHERE customer_id = ? AND status = 'open'", (customer["id"],)
    ).fetchone()
    if existing:
        return {"ok": True, "duplicate": True, "failureId": existing["id"],
                "emailsQueued": 0, "message": "Customer already has an open failure."}

    cur = conn.execute(
        "INSERT INTO failures (customer_id, amount_cents, currency, status, reason, source, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (customer["id"], amount_cents, currency, "open", reason, source, now()),
    )
    failure_id = cur.lastrowid
    queued = enroll(conn, failure_id, customer["email"], customer["name"] or "")
    conn.execute(
        "INSERT INTO events (failure_id, customer_id, kind, detail, created_at) VALUES (?,?,?,?,?)",
        (failure_id, customer["id"], "payment_failed", reason or "", now()),
    )
    conn.commit()
    return {"ok": True, "duplicate": False, "failureId": failure_id, "emailsQueued": queued,
            "customerId": customer["id"]}


def recover_failure(conn: sqlite3.Connection, failure_id: int, source: str = "manual") -> Dict[str, Any]:
    failure = conn.execute("SELECT * FROM failures WHERE id = ?", (failure_id,)).fetchone()
    if not failure:
        raise HTTPException(status_code=404, detail="Failure not found")
    if failure["status"] == "recovered":
        return {"ok": True, "alreadyRecovered": True, "failureId": failure_id}
    cancelled = conn.execute(
        "UPDATE emails SET status = 'cancelled' WHERE failure_id = ? AND status = 'queued'",
        (failure_id,),
    ).rowcount
    conn.execute(
        "UPDATE failures SET status = 'recovered', recovered_at = ? WHERE id = ?", (now(), failure_id)
    )
    conn.execute(
        "INSERT INTO events (failure_id, customer_id, kind, detail, created_at) VALUES (?,?,?,?,?)",
        (failure_id, failure["customer_id"], "recovered", source, now()),
    )
    conn.commit()
    return {"ok": True, "alreadyRecovered": False, "failureId": failure_id, "emailsCancelled": cancelled}


# ---------------------------------------------------------------------- routes
@app.get("/health")
def health() -> Dict[str, Any]:
    with closing(connect()) as conn:
        open_count = conn.execute("SELECT COUNT(*) c FROM failures WHERE status='open'").fetchone()["c"]
        queued = conn.execute("SELECT COUNT(*) c FROM emails WHERE status='queued'").fetchone()["c"]
    return {"status": "online", "service": "Churn Buster", "version": "0.1.0",
            "sendMode": SEND_MODE, "openFailures": open_count, "queuedEmails": queued,
            "stripeSignatureCheck": bool(STRIPE_WEBHOOK_SECRET)}


@app.post("/api/auth/login")
def login(body: Dict[str, str] = Body(...)) -> Dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    with closing(connect()) as conn:
        user = conn.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Wrong email or password")
        token = secrets.token_urlsafe(32)
        expires = iso(datetime.now(timezone.utc) + timedelta(hours=12))
        conn.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
                     (token, user["id"], expires))
        conn.commit()
    return {"token": token, "expiresAt": expires, "user": {"email": user["email"], "name": user["name"]}}


@app.post("/api/auth/logout")
def logout(user: Dict[str, Any] = Depends(current_user), authorization: str = Header(default="")) -> Dict[str, Any]:
    token = authorization.split(" ", 1)[1].strip()
    with closing(connect()) as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    return {"ok": True}


@app.get("/api/customers")
def list_customers(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM failures f WHERE f.customer_id=c.id AND f.status='open') open_failures"
            " FROM customers c ORDER BY c.email"
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@app.get("/api/failures")
def list_failures(status: Optional[str] = None, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    q = ("SELECT f.*, c.email, c.name, c.mrr_cents FROM failures f JOIN customers c ON c.id=f.customer_id")
    args: List[Any] = []
    if status:
        q += " WHERE f.status = ?"
        args.append(status)
    q += " ORDER BY f.id DESC LIMIT 200"
    with closing(connect()) as conn:
        rows = conn.execute(q, args).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@app.post("/api/simulate/payment-failure")
def simulate_failure(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        return open_failure(
            conn,
            body.get("customer_email", ""),
            int(body.get("amount_cents") or 0),
            body.get("customer_name"),
            body.get("reason") or "card_declined",
            "simulator",
            body.get("currency") or "usd",
            body.get("mrr_cents"),
        )


@app.post("/api/failures/{failure_id}/recover")
def recover(failure_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        return recover_failure(conn, failure_id, "manual")


@app.get("/api/campaigns")
def campaigns(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM campaigns ORDER BY id").fetchall()
        out = []
        for c in rows:
            steps = conn.execute(
                "SELECT position, delay_minutes, subject FROM campaign_steps WHERE campaign_id=? ORDER BY position",
                (c["id"],),
            ).fetchall()
            out.append({**dict(c), "steps": [dict(s) for s in steps]})
    return {"items": out}


@app.get("/api/emails")
def emails(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT e.*, c.email FROM emails e JOIN customers c ON c.id=e.customer_id"
            " ORDER BY e.id DESC LIMIT 200"
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@app.post("/api/dispatch")
def dispatch(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """Process every queued email whose due_at has passed. Dry run records, never sends."""
    processed = 0
    with closing(connect()) as conn:
        due = conn.execute(
            "SELECT * FROM emails WHERE status='queued' AND due_at <= ?", (now(),)
        ).fetchall()
        for e in due:
            status = "sent" if SEND_MODE == "smtp" else "dry_run"
            conn.execute(
                "UPDATE emails SET status=?, sent_at=? WHERE id=?",
                (status, now() if status == "sent" else None, e["id"]),
            )
            processed += 1
        conn.commit()
    return {"ok": True, "processed": processed, "mode": SEND_MODE}


@app.get("/api/analytics")
def analytics(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        total = conn.execute("SELECT COUNT(*) c FROM failures").fetchone()["c"]
        open_f = conn.execute("SELECT COUNT(*) c FROM failures WHERE status='open'").fetchone()["c"]
        recovered = conn.execute("SELECT COUNT(*) c FROM failures WHERE status='recovered'").fetchone()["c"]
        at_risk = conn.execute(
            "SELECT COALESCE(SUM(c.mrr_cents),0) s FROM failures f JOIN customers c ON c.id=f.customer_id"
            " WHERE f.status='open'"
        ).fetchone()["s"]
        recovered_cents = conn.execute(
            "SELECT COALESCE(SUM(c.mrr_cents),0) s FROM failures f JOIN customers c ON c.id=f.customer_id"
            " WHERE f.status='recovered'"
        ).fetchone()["s"]
        emails_sent = conn.execute("SELECT COUNT(*) c FROM emails WHERE status IN ('sent','dry_run')").fetchone()["c"]
        emails_queued = conn.execute("SELECT COUNT(*) c FROM emails WHERE status='queued'").fetchone()["c"]
    rate = round((recovered / total) * 100, 1) if total else 0.0
    return {
        "failures": total,
        "open": open_f,
        "recovered": recovered,
        "recoveryRatePct": rate,
        "mrrAtRiskCents": at_risk,
        "recoveredMrrCents": recovered_cents,
        "emailsSent": emails_sent,
        "emailsQueued": emails_queued,
        "sendMode": SEND_MODE,
    }


def verify_stripe(raw: bytes, signature: Optional[str]) -> bool:
    """Return True when the payload may be trusted. When no secret is set, accept and label it."""
    if not STRIPE_WEBHOOK_SECRET:
        return True
    if not signature:
        return False
    parts = dict(p.split("=", 1) for p in signature.split(",") if "=" in p)
    timestamp = parts.get("t", "")
    expected = hmac.new(
        STRIPE_WEBHOOK_SECRET.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    return secrets.compare_digest(expected, parts.get("v1", ""))


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, stripe_signature: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    raw = await request.body()
    if not verify_stripe(raw, stripe_signature):
        raise HTTPException(status_code=400, detail="Invalid signature")
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    event_type = payload.get("type") or ""
    obj = (payload.get("data") or {}).get("object") or {}
    email = (obj.get("customer_email") or obj.get("receipt_email")
             or (obj.get("customer") or {}).get("email") or "")
    amount = int(obj.get("amount_due") or obj.get("amount") or 0)
    source = "stripe" if STRIPE_WEBHOOK_SECRET else "stripe-sim"
    with closing(connect()) as conn:
        if event_type in ("invoice.payment_failed", "charge.failed"):
            result = open_failure(conn, email, amount, None, "stripe_invoice_failed", source)
            return {"received": True, "action": "failure_opened", **result}
        if event_type in ("invoice.paid", "charge.succeeded", "invoice.payment_succeeded"):
            customer = conn.execute("SELECT * FROM customers WHERE lower(email)=?", (email.lower(),)).fetchone()
            if not customer:
                return {"received": True, "action": "ignored", "reason": "unknown customer"}
            failure = conn.execute(
                "SELECT * FROM failures WHERE customer_id=? AND status='open' ORDER BY id DESC LIMIT 1",
                (customer["id"],),
            ).fetchone()
            if not failure:
                return {"received": True, "action": "ignored", "reason": "no open failure"}
            result = recover_failure(conn, failure["id"], source)
            return {"received": True, "action": "recovered", **result}
        return {"received": True, "action": "ignored", "type": event_type}


ADMIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Churn Buster - Admin</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{background:#090b10;color:#e6e9ef;font:14px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0}
header{border-bottom:1px solid #1d2330;padding:14px 22px;display:flex;justify-content:space-between}
h1{font-size:16px;margin:0}.muted{color:#8b93a3}
main{padding:22px;max-width:1100px;margin:0 auto}
input{background:#0b0d12;border:1px solid #2a3243;color:#e6e9ef;border-radius:6px;padding:7px 9px;margin-right:6px}
button{background:#2f6feb;border:0;color:#fff;border-radius:6px;padding:7px 13px;cursor:pointer;font-weight:600}
table{width:100%;border-collapse:collapse;margin-top:14px;font-size:13px}
th{text-align:left;color:#8b93a3;border-bottom:1px solid #1d2330;padding:8px 6px}
td{border-bottom:1px solid #141922;padding:8px 6px}
.stats{display:flex;gap:12px;flex-wrap:wrap}
.stat{border:1px solid #1d2330;border-radius:8px;padding:10px 14px;background:#0e1117;min-width:110px}
.stat b{display:block;font-size:19px}.err{color:#ff8f8f}.ok{color:#6ee7b7}
</style></head><body>
<header><h1>Churn Buster <span class="muted">admin</span></h1><span class="muted" id="who"></span></header>
<main>
<div id="login"><p class="muted">admin@churnbuster.local / churnbuster123</p>
<input id="email" value="admin@churnbuster.local"><input id="password" type="password" placeholder="password">
<button onclick="signin()">Sign in</button> <span id="loginmsg" class="err"></span></div>
<div id="panel" style="display:none">
  <div class="stats" id="stats"></div>
  <p style="margin-top:18px"><input id="sim-email" placeholder="customer@example.com"><input id="sim-amount" placeholder="amount cents" size="12">
  <button onclick="simulate()">Simulate failed payment</button> <button onclick="dispatch()">Dispatch due emails</button> <span id="msg" class="ok"></span></p>
  <h3 style="font-size:14px">Open failures</h3>
  <table><thead><tr><th>#</th><th>Customer</th><th>Amount</th><th>MRR</th><th>Reason</th><th>Source</th><th></th></tr></thead><tbody id="failures"></tbody></table>
  <h3 style="font-size:14px;margin-top:22px">Queued and sent emails</h3>
  <table><thead><tr><th>#</th><th>To</th><th>Subject</th><th>Status</th><th>Due</th></tr></thead><tbody id="emails"></tbody></table>
</div></main>
<script>
let token=localStorage.getItem('cb_token')||'';
const H=()=>({'Content-Type':'application/json','Authorization':'Bearer '+token});
async function signin(){
  const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('email').value,password:document.getElementById('password').value})});
  const d=await r.json(); if(!r.ok){document.getElementById('loginmsg').textContent=d.detail||'failed';return;}
  token=d.token;localStorage.setItem('cb_token',token);boot();
}
async function boot(){
  if(!token)return;
  const a=await fetch('/api/analytics',{headers:H()}); if(!a.ok){document.getElementById('login').style.display='block';document.getElementById('panel').style.display='none';return;}
  document.getElementById('login').style.display='none';document.getElementById('panel').style.display='block';document.getElementById('who').textContent='signed in';
  const s=await a.json();
  document.getElementById('stats').innerHTML=[['Failures',s.failures],['Open',s.open],['Recovered',s.recovered],['Recovery %',s.recoveryRatePct],['MRR at risk',(s.mrrAtRiskCents/100).toFixed(2)],['Recovered MRR',(s.recoveredMrrCents/100).toFixed(2)]]
    .map(([k,v])=>`<div class="stat"><span class="muted">${k}</span><b>${v}</b></div>`).join('');
  const f=await (await fetch('/api/failures?status=open',{headers:H()})).json();
  document.getElementById('failures').innerHTML=f.items.map(x=>`<tr><td>${x.id}</td><td>${x.email}</td><td>${(x.amount_cents/100).toFixed(2)}</td><td>${(x.mrr_cents/100).toFixed(2)}</td><td>${x.reason||''}</td><td>${x.source}</td><td><button onclick="recover(${x.id})">Mark recovered</button></td></tr>`).join('')||'<tr><td colspan="7" class="muted">none</td></tr>';
  const e=await (await fetch('/api/emails',{headers:H()})).json();
  document.getElementById('emails').innerHTML=e.items.slice(0,20).map(x=>`<tr><td>${x.id}</td><td>${x.to_address}</td><td>${x.subject}</td><td>${x.status}</td><td>${x.due_at}</td></tr>`).join('')||'<tr><td colspan="5" class="muted">none</td></tr>';
}
async function simulate(){
  const r=await fetch('/api/simulate/payment-failure',{method:'POST',headers:H(),body:JSON.stringify({customer_email:document.getElementById('sim-email').value,amount_cents:Number(document.getElementById('sim-amount').value||4900)})});
  const d=await r.json();document.getElementById('msg').textContent=r.ok?('queued '+d.emailsQueued+' emails'):d.detail;boot();
}
async function dispatch(){
  const r=await fetch('/api/dispatch',{method:'POST',headers:H(),body:'{}'});const d=await r.json();
  document.getElementById('msg').textContent='processed '+d.processed;boot();
}
async function recover(id){await fetch('/api/failures/'+id+'/recover',{method:'POST',headers:H(),body:'{}'});boot();}
boot();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def admin_console() -> HTMLResponse:
    return HTMLResponse(ADMIN_PAGE)


@app.get("/api/report.csv", response_class=PlainTextResponse)
def report_csv(user: Dict[str, Any] = Depends(current_user)) -> PlainTextResponse:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT f.id, c.email, f.amount_cents, c.mrr_cents, f.status, f.source, f.created_at,"
            " COALESCE(f.recovered_at,'') FROM failures f JOIN customers c ON c.id=f.customer_id ORDER BY f.id"
        ).fetchall()
    lines = ["failure_id,email,amount_cents,mrr_cents,status,source,created_at,recovered_at"]
    for r in rows:
        lines.append(",".join(str(v) for v in tuple(r)))
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="failures.csv"'})
