"""Smile.io replica: points ledger, referrals and VIP tiers.

The dangerous part of a loyalty system is the ledger, so this build makes it append-only and
derives every balance from entries. There is no mutable balance column to drift out of sync.

Guarantees enforced here:
- balance = sum(earn) - sum(redeem), computed from the ledger, never stored
- a redemption that would go negative is refused and writes nothing
- every mutation requires an idempotency key, so a retried request cannot double-award
- self-referral and duplicate referrals are refused
- tier is derived from lifetime earned points, not current balance
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("SMILE_DB", BASE_DIR / "data" / "smile.db"))
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@smile.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PASSWORD_ROUNDS = 240_000

TIERS = [("bronze", 0), ("silver", 500), ("gold", 1500), ("platinum", 5000)]
REFERRER_BONUS = 200
REFEREE_BONUS = 100

app = FastAPI(title="Smile Engine", version="0.1.0")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    referral_code TEXT UNIQUE NOT NULL,
    referred_by TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,               -- earn | redeem | adjust
    points INTEGER NOT NULL,          -- positive for earn, negative for redeem
    reason TEXT,
    idempotency_key TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rewards (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cost_points INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS redemptions (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    reward_id TEXT NOT NULL,
    points INTEGER NOT NULL,
    status TEXT NOT NULL,
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


def seed(conn: sqlite3.Connection) -> None:
    for rid, name, cost in [("rw_latte", "Free latte", 150), ("rw_beanie", "Merino beanie", 400),
                            ("rw_hoodie", "Logo hoodie", 1200)]:
        conn.execute("INSERT INTO rewards (id, name, cost_points, active) VALUES (?,?,?,1)",
                     (rid, name, cost))
    demo = [("maya@example.com", "Maya Okafor", 820), ("dev@example.com", "Dev Patel", 120),
            ("sam@example.com", "Sam Rivera", 2400)]
    for i, (email, name, pts) in enumerate(demo):
        mid = f"mem_{i:03d}"
        conn.execute("INSERT INTO members (id, email, name, referral_code, referred_by, created_at)"
                     " VALUES (?,?,?,?,NULL,?)", (mid, email, name, f"REF{1000 + i}", now()))
        conn.execute("INSERT INTO ledger (member_id, kind, points, reason, idempotency_key, created_at)"
                     " VALUES (?,?,?,?,?,?)", (mid, "earn", pts, "seed", f"seed-{mid}", now()))
    conn.commit()


def init_db() -> None:
    with closing(connect()) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
        if conn.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone():
            return
        if not ADMIN_PASSWORD:
            raise RuntimeError(
                "No admin account exists and ADMIN_PASSWORD is not set.\n"
                "Set it before the first start, for example:\n"
                "  export ADMIN_PASSWORD='choose-a-long-unique-password'\n"
                "No default password ships with this service."
            )
        conn.execute("INSERT INTO users (email, password_hash, created_at) VALUES (?,?,?)",
                     (ADMIN_EMAIL, hash_password(ADMIN_PASSWORD), now()))
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
            "SELECT s.expires_at, u.id FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (token,),
        ).fetchone()
    if not row or row["expires_at"] < now():
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return {"id": row["id"]}


# ------------------------------------------------------------------ ledger core
def balance_of(conn: sqlite3.Connection, member_id: str) -> int:
    row = conn.execute("SELECT COALESCE(SUM(points),0) s FROM ledger WHERE member_id=?",
                       (member_id,)).fetchone()
    return int(row["s"])


def lifetime_earned(conn: sqlite3.Connection, member_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(points),0) s FROM ledger WHERE member_id=? AND points > 0",
        (member_id,)).fetchone()
    return int(row["s"])


def tier_for(points: int) -> Dict[str, Any]:
    name, threshold = TIERS[0]
    for tname, tthresh in TIERS:
        if points >= tthresh:
            name, threshold = tname, tthresh
    idx = [t[0] for t in TIERS].index(name)
    nxt = TIERS[idx + 1] if idx + 1 < len(TIERS) else None
    return {"tier": name, "threshold": threshold,
            "nextTier": nxt[0] if nxt else None,
            "pointsToNextTier": (nxt[1] - points) if nxt else 0}


def member_summary(conn: sqlite3.Connection, member_id: str) -> Dict[str, Any]:
    m = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
    balance = balance_of(conn, member_id)
    earned = lifetime_earned(conn, member_id)
    return {"id": m["id"], "email": m["email"], "name": m["name"],
            "referralCode": m["referral_code"], "referredBy": m["referred_by"],
            "balance": balance, "lifetimeEarned": earned, **tier_for(earned)}


def award(conn: sqlite3.Connection, member_id: str, points: int, reason: str,
          idempotency_key: str) -> Dict[str, Any]:
    existing = conn.execute("SELECT * FROM ledger WHERE idempotency_key=?", (idempotency_key,)).fetchone()
    if existing:
        return {"ok": True, "duplicate": True, "entryId": existing["id"],
                "balance": balance_of(conn, member_id)}
    conn.execute(
        "INSERT INTO ledger (member_id, kind, points, reason, idempotency_key, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (member_id, "earn" if points >= 0 else "adjust", points, reason, idempotency_key, now()),
    )
    conn.commit()
    return {"ok": True, "duplicate": False, "points": points, "balance": balance_of(conn, member_id)}


@app.get("/health")
def health() -> Dict[str, Any]:
    with closing(connect()) as conn:
        members = conn.execute("SELECT COUNT(*) c FROM members").fetchone()["c"]
        entries = conn.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"]
        points = conn.execute("SELECT COALESCE(SUM(points),0) s FROM ledger").fetchone()["s"]
    return {"status": "online", "service": "Smile Engine", "version": "0.1.0",
            "members": members, "ledgerEntries": entries, "pointsOutstanding": points}


@app.post("/api/auth/login")
def login(body: Dict[str, str] = Body(...)) -> Dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    with closing(connect()) as conn:
        user = conn.execute("SELECT * FROM users WHERE lower(email)=?", (email,)).fetchone()
        if not user or not verify_password(body.get("password") or "", user["password_hash"]):
            raise HTTPException(status_code=401, detail="Wrong email or password")
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(timespec="seconds")
        conn.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
                     (token, user["id"], expires))
        conn.commit()
    return {"token": token, "expiresAt": expires}


@app.get("/api/members")
def list_members(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM members ORDER BY id").fetchall()]
        items = [member_summary(conn, mid) for mid in ids]
    return {"items": items, "total": len(items)}


@app.post("/api/members")
def create_member(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="valid email is required")
    with closing(connect()) as conn:
        if conn.execute("SELECT id FROM members WHERE lower(email)=?", (email,)).fetchone():
            raise HTTPException(status_code=409, detail="Member already exists")
        mid = "mem_" + secrets.token_hex(6)
        code = "REF" + secrets.token_hex(4).upper()
        conn.execute("INSERT INTO members (id, email, name, referral_code, referred_by, created_at)"
                     " VALUES (?,?,?,?,?,?)",
                     (mid, email, body.get("name") or email.split("@")[0].title(), code, None, now()))
        conn.commit()
        return member_summary(conn, mid)


@app.get("/api/members/{member_id}")
def get_member(member_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        return member_summary(conn, member_id)


@app.post("/api/members/{member_id}/earn")
def earn(member_id: str, body: Dict[str, Any] = Body(...), user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    points = body.get("points")
    key = (body.get("idempotency_key") or "").strip()
    if not isinstance(points, int) or points <= 0:
        raise HTTPException(status_code=422, detail="points must be a positive integer")
    if not key:
        raise HTTPException(status_code=422, detail="idempotency_key is required")
    with closing(connect()) as conn:
        if not conn.execute("SELECT id FROM members WHERE id=?", (member_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Member not found")
        return award(conn, member_id, points, body.get("reason") or "manual", key)


@app.post("/api/members/{member_id}/redeem")
def redeem(member_id: str, body: Dict[str, Any] = Body(...), user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    reward_id = (body.get("reward_id") or "").strip()
    key = (body.get("idempotency_key") or "").strip()
    if not reward_id or not key:
        raise HTTPException(status_code=422, detail="reward_id and idempotency_key are required")
    with closing(connect()) as conn:
        existing = conn.execute("SELECT * FROM ledger WHERE idempotency_key=?", (key,)).fetchone()
        if existing:
            return {"ok": True, "duplicate": True, "entryId": existing["id"],
                    "balance": balance_of(conn, member_id)}
        reward = conn.execute("SELECT * FROM rewards WHERE id=? AND active=1", (reward_id,)).fetchone()
        if not reward:
            raise HTTPException(status_code=404, detail="Reward not found")
        if not conn.execute("SELECT id FROM members WHERE id=?", (member_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Member not found")
        cost = int(reward["cost_points"])
        current = balance_of(conn, member_id)
        if current < cost:
            # refuse and write nothing
            raise HTTPException(status_code=409,
                                detail=f"Insufficient points: balance {current}, cost {cost}")
        rid = "red_" + secrets.token_hex(6)
        conn.execute("INSERT INTO redemptions (id, member_id, reward_id, points, status, created_at)"
                     " VALUES (?,?,?,?,?,?)", (rid, member_id, reward_id, cost, "fulfilled", now()))
        conn.execute("INSERT INTO ledger (member_id, kind, points, reason, idempotency_key, created_at)"
                     " VALUES (?,?,?,?,?,?)",
                     (member_id, "redeem", -cost, f"redeem:{reward['name']}", key, now()))
        conn.commit()
        return {"ok": True, "duplicate": False, "redemptionId": rid, "points": -cost,
                "balance": balance_of(conn, member_id)}


@app.get("/api/members/{member_id}/ledger")
def ledger(member_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM ledger WHERE member_id=? ORDER BY id", (member_id,)).fetchall()
        return {"items": [dict(r) for r in rows], "total": len(rows),
                "balance": balance_of(conn, member_id)}


@app.post("/api/referrals")
def referral(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    code = (body.get("referral_code") or "").strip().upper()
    referee_email = (body.get("referee_email") or "").strip().lower()
    key = (body.get("idempotency_key") or "").strip()
    if not code or not referee_email or not key:
        raise HTTPException(status_code=422, detail="referral_code, referee_email and idempotency_key are required")
    with closing(connect()) as conn:
        referrer = conn.execute("SELECT * FROM members WHERE referral_code=?", (code,)).fetchone()
        if not referrer:
            raise HTTPException(status_code=404, detail="Referral code not found")
        if referee_email == referrer["email"].lower():
            raise HTTPException(status_code=409, detail="Self-referral is not allowed")
        if conn.execute("SELECT id FROM members WHERE lower(email)=?", (referee_email,)).fetchone():
            raise HTTPException(status_code=409, detail="Referee already a member")
        mid = "mem_" + secrets.token_hex(6)
        new_code = "REF" + secrets.token_hex(4).upper()
        conn.execute("INSERT INTO members (id, email, name, referral_code, referred_by, created_at)"
                     " VALUES (?,?,?,?,?,?)",
                     (mid, referee_email, referee_email.split("@")[0].title(), new_code,
                      referrer["id"], now()))
        referrer_award = award(conn, referrer["id"], REFERRER_BONUS, f"referral:{referee_email}", f"{key}:referrer")
        referee_award = award(conn, mid, REFEREE_BONUS, f"referred_by:{referrer['email']}", f"{key}:referee")
        conn.commit()
        return {"ok": True, "refereeId": mid, "referrerId": referrer["id"],
                "referrerBonus": REFERRER_BONUS, "refereeBonus": REFEREE_BONUS,
                "referrerBalance": referrer_award["balance"],
                "refereeBalance": referee_award["balance"]}


@app.get("/api/rewards")
def rewards(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM rewards ORDER BY cost_points").fetchall()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/tiers")
def tiers(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return {"items": [{"name": n, "threshold": t} for n, t in TIERS]}


@app.get("/api/report.csv", response_class=PlainTextResponse)
def report_csv(user: Dict[str, Any] = Depends(current_user)) -> PlainTextResponse:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT l.id, m.email, l.kind, l.points, l.reason, l.created_at FROM ledger l"
            " JOIN members m ON m.id=l.member_id ORDER BY l.id"
        ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["entry_id", "email", "kind", "points", "reason", "created_at"])
    for r in rows:
        w.writerow([r["id"], r["email"], r["kind"], r["points"], r["reason"], r["created_at"]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="ledger.csv"'})


CONSOLE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Smile Engine</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{background:#090b10;color:#e6e9ef;font:14px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0}
header{border-bottom:1px solid #1d2330;padding:14px 22px}h1{font-size:16px;margin:0}
main{padding:22px;max-width:1000px;margin:0 auto}
input{background:#0b0d12;border:1px solid #2a3243;color:#e6e9ef;border-radius:6px;padding:7px 9px;margin-right:6px}
button{background:#2f6feb;border:0;color:#fff;border-radius:6px;padding:7px 13px;cursor:pointer;font-weight:600}
table{width:100%;border-collapse:collapse;margin-top:14px;font-size:13px}
th{text-align:left;color:#8b93a3;border-bottom:1px solid #1d2330;padding:8px 6px}
td{border-bottom:1px solid #141922;padding:8px 6px}.muted{color:#8b93a3}.err{color:#ff8f8f}
</style></head><body>
<header><h1>Smile Engine <span class="muted">points, referrals, tiers</span></h1></header>
<main>
<div id="login"><p class="muted">Sign in with the admin password you configured at first start.</p>
<input id="email" value="admin@smile.local"><input id="password" type="password" placeholder="password">
<button onclick="signin()">Sign in</button> <span id="loginmsg" class="err"></span></div>
<div id="panel" style="display:none">
<p class="muted" id="summary"></p>
<table><thead><tr><th>Member</th><th>Balance</th><th>Lifetime earned</th><th>Tier</th><th>Next tier</th><th>Referral code</th></tr></thead><tbody id="rows"></tbody></table>
</div></main>
<script>
let token=localStorage.getItem('sm_token')||'';
const H=()=>({'Content-Type':'application/json','Authorization':'Bearer '+token});
async function signin(){
  const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('email').value,password:document.getElementById('password').value})});
  const d=await r.json(); if(!r.ok){document.getElementById('loginmsg').textContent=d.detail||'failed';return;}
  token=d.token;localStorage.setItem('sm_token',token);boot();
}
async function boot(){
  if(!token)return;
  const r=await fetch('/api/members',{headers:H()});
  if(!r.ok){document.getElementById('login').style.display='block';document.getElementById('panel').style.display='none';return;}
  document.getElementById('login').style.display='none';document.getElementById('panel').style.display='block';
  const d=await r.json();
  document.getElementById('summary').textContent=d.total+' members. Balances are derived from the append-only ledger.';
  document.getElementById('rows').innerHTML=d.items.map(m=>`<tr><td>${m.email}</td><td>${m.balance}</td><td>${m.lifetimeEarned}</td><td>${m.tier}</td><td>${m.nextTier||'top tier'}${m.nextTier?' ('+m.pointsToNextTier+' pts)':''}</td><td>${m.referralCode}</td></tr>`).join('');
}
boot();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def console() -> HTMLResponse:
    return HTMLResponse(CONSOLE)
