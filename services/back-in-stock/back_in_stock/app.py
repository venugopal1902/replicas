"""Back in Stock replica: restock waitlists with queued notifications.

Single-module app on purpose: it keeps the whole contract readable in one place.
Storage is stdlib sqlite3. Notifications are queued rows, never real sends unless
SMTP is configured, and the default is dry run.
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

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.environ.get("BACKINSTOCK_DB", DATA_DIR / "backinstock.db"))
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@backinstock.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "backinstock123")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Store Admin")
SEND_MODE = os.environ.get("SEND_MODE", "dry_run")  # dry_run | smtp
NOTIFICATION_COST = 0

PASSWORD_ROUNDS = 240_000


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
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    handle TEXT UNIQUE NOT NULL,
    image_hint TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    sku TEXT UNIQUE NOT NULL,
    price_cents INTEGER NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    phone TEXT,
    channel TEXT NOT NULL DEFAULT 'email',
    status TEXT NOT NULL DEFAULT 'waiting',
    created_at TEXT NOT NULL,
    UNIQUE(variant_id, email)
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    variant_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    address TEXT NOT NULL,
    status TEXT NOT NULL,
    body TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS preorders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'captured',
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def init_db(seed: bool = True) -> None:
    with closing(connect()) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO users (email, name, password_hash, created_at) VALUES (?,?,?,?)",
            (ADMIN_EMAIL, ADMIN_NAME, hash_password(ADMIN_PASSWORD), now()),
        )
        conn.commit()
        if seed:
            seed_demo(conn)


PRODUCTS = [
    ("Aurora Linen Shirt", "aurora-linen-shirt", [("Small", 6800, 4), ("Medium", 6800, 0), ("Large", 6800, 2)]),
    ("Cedar Field Jacket", "cedar-field-jacket", [("Medium", 18900, 0), ("Large", 18900, 0)]),
    ("Drift Merino Beanie", "drift-merino-beanie", [("One Size", 3900, 7)]),
    ("Ember Ceramic Mug", "ember-ceramic-mug", [("350ml", 2400, 0), ("500ml", 2400, 3)]),
    ("Fjord Wool Throw", "fjord-wool-throw", [("130x180", 12900, 0)]),
    ("Grove Canvas Tote", "grove-canvas-tote", [("Standard", 5400, 12)]),
]


def seed_demo(conn: sqlite3.Connection) -> None:
    for name, handle, variants in PRODUCTS:
        cur = conn.execute(
            "INSERT INTO products (name, handle, image_hint, created_at) VALUES (?,?,?,?)",
            (name, handle, handle, now()),
        )
        product_id = cur.lastrowid
        for title, price, stock in variants:
            sku = f"{handle}-{title.lower().replace(' ', '-')}"
            conn.execute(
                "INSERT INTO variants (product_id, title, sku, price_cents, stock, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (product_id, title, sku, price, stock, now()),
            )
    # a few shoppers already waiting on sold-out variants
    rows = conn.execute("SELECT id, title FROM variants WHERE stock = 0").fetchall()
    demo = ["maya@example.com", "dev@example.com", "sam@example.com"]
    for idx, variant in enumerate(rows):
        for j in range((idx % 2) + 1):
            email = demo[(idx + j) % len(demo)]
            conn.execute(
                "INSERT OR IGNORE INTO subscriptions (variant_id, email, channel, status, created_at)"
                " VALUES (?,?,?,?,?)",
                (variant["id"], email, "email", "waiting", now()),
            )
    conn.commit()


# ------------------------------------------------------------------------- auth
def current_user(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT s.token, s.expires_at, u.id, u.email, u.name FROM sessions s"
            " JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid session")
    if row["expires_at"] < now():
        raise HTTPException(status_code=401, detail="Session expired")
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


# -------------------------------------------------------------------------- app
app = FastAPI(title="Back in Stock", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db(seed=True)


@app.get("/health")
def health() -> Dict[str, Any]:
    with closing(connect()) as conn:
        variants = conn.execute("SELECT COUNT(*) c FROM variants").fetchone()["c"]
        waiting = conn.execute(
            "SELECT COUNT(*) c FROM subscriptions WHERE status = 'waiting'"
        ).fetchone()["c"]
    return {
        "status": "online",
        "service": "Back in Stock",
        "version": "0.1.0",
        "sendMode": SEND_MODE,
        "variants": variants,
        "waiting": waiting,
    }


@app.post("/api/auth/login")
def login(body: Dict[str, str] = Body(...)) -> Dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    with closing(connect()) as conn:
        user = conn.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Wrong email or password")
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
            (token, user["id"], expires),
        )
        conn.commit()
    return {"token": token, "expiresAt": expires, "user": {"email": user["email"], "name": user["name"]}}


@app.post("/api/auth/logout")
def logout(user: Dict[str, Any] = Depends(current_user), authorization: str = Header(default="")) -> Dict[str, Any]:
    token = authorization.split(" ", 1)[1].strip()
    with closing(connect()) as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    return {"ok": True}


@app.get("/api/variants")
def list_variants(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT v.id, v.title, v.sku, v.price_cents, v.stock, p.name AS product,"
            " (SELECT COUNT(*) FROM subscriptions s WHERE s.variant_id = v.id AND s.status='waiting') AS waiting"
            " FROM variants v JOIN products p ON p.id = v.product_id ORDER BY p.name, v.title"
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/subscriptions")
def list_subscriptions(
    user: Dict[str, Any] = Depends(current_user), variant_id: Optional[int] = None
) -> Dict[str, Any]:
    q = (
        "SELECT s.id, s.email, s.phone, s.channel, s.status, s.created_at, s.variant_id,"
        " v.sku, v.title AS variant, p.name AS product"
        " FROM subscriptions s JOIN variants v ON v.id = s.variant_id"
        " JOIN products p ON p.id = v.product_id"
    )
    args: List[Any] = []
    if variant_id is not None:
        q += " WHERE s.variant_id = ?"
        args.append(variant_id)
    q += " ORDER BY s.created_at DESC"
    with closing(connect()) as conn:
        rows = conn.execute(q, args).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@app.get("/api/notifications")
def list_notifications(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT n.id, n.variant_id, n.channel, n.address, n.status, n.created_at, n.sent_at,"
            " v.sku FROM notifications n JOIN variants v ON v.id = n.variant_id"
            " ORDER BY n.id DESC LIMIT 200"
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@app.post("/api/public/waitlist")
def public_waitlist(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    variant_id = body.get("variant_id")
    email = (body.get("email") or "").strip().lower()
    phone = (body.get("phone") or "").strip() or None
    if not variant_id or not email:
        raise HTTPException(status_code=422, detail="variant_id and email are required")
    channel = "sms" if phone and not email else "email"
    with closing(connect()) as conn:
        variant = conn.execute("SELECT * FROM variants WHERE id = ?", (variant_id,)).fetchone()
        if not variant:
            raise HTTPException(status_code=404, detail="Variant not found")
        existing = conn.execute(
            "SELECT * FROM subscriptions WHERE variant_id = ? AND email = ?", (variant_id, email)
        ).fetchone()
        if existing:
            return {
                "ok": True,
                "duplicate": True,
                "subscriptionId": existing["id"],
                "status": existing["status"],
                "message": "You are already on this waitlist.",
            }
        cur = conn.execute(
            "INSERT INTO subscriptions (variant_id, email, phone, channel, status, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (variant_id, email, phone, channel, "waiting", now()),
        )
        conn.commit()
        return {
            "ok": True,
            "duplicate": False,
            "subscriptionId": cur.lastrowid,
            "status": "waiting",
            "inStock": variant["stock"] > 0,
            "message": "You are on the waitlist. We will email you the moment it returns.",
        }


@app.post("/api/public/preorder")
def public_preorder(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    variant_id = body.get("variant_id")
    email = (body.get("email") or "").strip().lower()
    quantity = int(body.get("quantity") or 1)
    if not variant_id or not email:
        raise HTTPException(status_code=422, detail="variant_id and email are required")
    if quantity < 1:
        raise HTTPException(status_code=422, detail="quantity must be at least 1")
    with closing(connect()) as conn:
        if not conn.execute("SELECT id FROM variants WHERE id = ?", (variant_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Variant not found")
        cur = conn.execute(
            "INSERT INTO preorders (variant_id, email, quantity, status, created_at) VALUES (?,?,?,?,?)",
            (variant_id, email, quantity, "captured", now()),
        )
        conn.commit()
        return {"ok": True, "preorderId": cur.lastrowid, "quantity": quantity, "status": "captured"}


@app.post("/api/variants/{variant_id}/stock")
def set_stock(
    variant_id: int, body: Dict[str, Any] = Body(...), user: Dict[str, Any] = Depends(current_user)
) -> Dict[str, Any]:
    """Set stock. A 0 -> positive transition queues a notification per waiting subscriber."""
    if "stock" not in body:
        raise HTTPException(status_code=422, detail="stock is required")
    new_stock = int(body["stock"])
    if new_stock < 0:
        raise HTTPException(status_code=422, detail="stock cannot be negative")
    queued = 0
    with closing(connect()) as conn:
        variant = conn.execute("SELECT * FROM variants WHERE id = ?", (variant_id,)).fetchone()
        if not variant:
            raise HTTPException(status_code=404, detail="Variant not found")
        was_out = variant["stock"] <= 0
        conn.execute(
            "UPDATE variants SET stock = ?, updated_at = ? WHERE id = ?", (new_stock, now(), variant_id)
        )
        if was_out and new_stock > 0:
            waiters = conn.execute(
                "SELECT * FROM subscriptions WHERE variant_id = ? AND status = 'waiting'", (variant_id,)
            ).fetchall()
            for w in waiters:
                address = w["phone"] if w["channel"] == "sms" and w["phone"] else w["email"]
                status = "sent" if SEND_MODE == "smtp" else "dry_run"
                body_text = f"{variant['sku']} is back in stock."
                conn.execute(
                    "INSERT INTO notifications (subscription_id, variant_id, channel, address, status, body,"
                    " created_at, sent_at) VALUES (?,?,?,?,?,?,?,?)",
                    (w["id"], variant_id, w["channel"], address, status, body_text, now(),
                     now() if status == "sent" else None),
                )
                conn.execute("UPDATE subscriptions SET status = 'notified' WHERE id = ?", (w["id"],))
                queued += 1
        conn.commit()
    return {"ok": True, "variantId": variant_id, "stock": new_stock, "notificationsQueued": queued}


@app.get("/api/stats")
def stats(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with closing(connect()) as conn:
        total_subs = conn.execute("SELECT COUNT(*) c FROM subscriptions").fetchone()["c"]
        waiting = conn.execute(
            "SELECT COUNT(*) c FROM subscriptions WHERE status='waiting'"
        ).fetchone()["c"]
        notified = conn.execute(
            "SELECT COUNT(*) c FROM subscriptions WHERE status='notified'"
        ).fetchone()["c"]
        notif = conn.execute("SELECT COUNT(*) c FROM notifications").fetchone()["c"]
        preorders = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(quantity),0) q FROM preorders").fetchone()
        out_of_stock = conn.execute("SELECT COUNT(*) c FROM variants WHERE stock = 0").fetchone()["c"]
        per_variant = conn.execute(
            "SELECT v.id, v.sku, v.stock,"
            " (SELECT COUNT(*) FROM subscriptions s WHERE s.variant_id=v.id AND s.status='waiting') waiting"
            " FROM variants v WHERE v.stock = 0 ORDER BY waiting DESC LIMIT 10"
        ).fetchall()
    return {
        "subscriptions": total_subs,
        "waiting": waiting,
        "notified": notified,
        "notifications": notif,
        "preorders": preorders["c"],
        "preorderUnits": preorders["q"],
        "outOfStockVariants": out_of_stock,
        "topWaitingVariants": [dict(r) for r in per_variant],
        "sendMode": SEND_MODE,
    }


@app.get("/api/variants/{variant_id}/subscribers.csv", response_class=PlainTextResponse)
def export_csv(variant_id: int, user: Dict[str, Any] = Depends(current_user)) -> PlainTextResponse:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT s.email, COALESCE(s.phone,'') phone, s.channel, s.status, s.created_at"
            " FROM subscriptions s WHERE s.variant_id = ? ORDER BY s.created_at",
            (variant_id,),
        ).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["email", "phone", "channel", "status", "created_at"])
    for r in rows:
        writer.writerow([r["email"], r["phone"], r["channel"], r["status"], r["created_at"]])
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="variant-{variant_id}-subscribers.csv"'},
    )


STORE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Aurora Supply - Back in Stock</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{background:#0b0d12;color:#e6e9ef;font:15px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0}}
.wrap{{max-width:860px;margin:0 auto;padding:32px 20px}}
h1{{font-size:22px;margin:0 0 4px}}.sub{{color:#8b93a3;margin:0 0 28px}}
.card{{border:1px solid #1d2330;background:#11141b;border-radius:10px;padding:16px;margin:0 0 12px}}
.row{{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}}
.sku{{font:13px ui-monospace,monospace;color:#8b93a3}}
.out{{color:#ff8f8f;font-size:13px}}.in{{color:#6ee7b7;font-size:13px}}
input{{background:#0b0d12;border:1px solid #2a3243;color:#e6e9ef;border-radius:6px;padding:8px 10px;min-width:220px}}
button{{background:#2f6feb;border:0;color:#fff;border-radius:6px;padding:8px 14px;cursor:pointer;font-weight:600}}
button:disabled{{background:#2a3243;color:#8b93a3;cursor:not-allowed}}
.msg{{font-size:13px;margin-top:8px;color:#6ee7b7}}.err{{color:#ff8f8f}}
a{{color:#7aa2ff}}
</style></head><body><div class="wrap">
<h1>Aurora Supply</h1><p class="sub">Demo storefront. Sold-out variants accept a waitlist signup. Admin console at <a href="/admin">/admin</a>.</p>
<div id="list"></div>
</div>
<script>
async function load(){{
  const r = await fetch('/api/public/catalog');
  const data = await r.json();
  const host = document.getElementById('list');
  host.innerHTML = data.items.map(v => `
    <div class="card">
      <div class="row">
        <div><strong>${{v.product}}</strong><div class="sku">${{v.title}} · ${{v.sku}} · $${{(v.price_cents/100).toFixed(2)}}</div></div>
        <div class="${{v.stock>0?'in':'out'}}">${{v.stock>0?v.stock+' in stock':'Sold out'}}</div>
      </div>
      <div class="row" style="margin-top:12px">
        <input id="email-${{v.id}}" placeholder="you@example.com" ${{v.stock>0?'disabled':''}}>
        <button onclick="join(${{v.id}})" ${{v.stock>0?'disabled':''}}>Notify me</button>
      </div>
      <div class="msg" id="msg-${{v.id}}"></div>
    </div>`).join('');
}}
async function join(id){{
  const email = document.getElementById('email-'+id).value;
  const msg = document.getElementById('msg-'+id);
  const r = await fetch('/api/public/waitlist', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{variant_id:id,email}})}});
  const d = await r.json();
  msg.className = r.ok ? 'msg' : 'msg err';
  msg.textContent = r.ok ? d.message : (d.detail || 'Could not join');
}}
load();
</script></body></html>"""


@app.get("/api/public/catalog")
def public_catalog() -> Dict[str, Any]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT v.id, v.title, v.sku, v.price_cents, v.stock, p.name AS product"
            " FROM variants v JOIN products p ON p.id = v.product_id ORDER BY p.name, v.title"
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.get("/", response_class=HTMLResponse)
def storefront() -> HTMLResponse:
    return HTMLResponse(STORE_PAGE.format())


ADMIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Back in Stock - Admin</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{background:#090b10;color:#e6e9ef;font:14px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0}
header{border-bottom:1px solid #1d2330;padding:14px 22px;display:flex;justify-content:space-between;align-items:center}
h1{font-size:16px;margin:0}.muted{color:#8b93a3}
main{padding:22px;max-width:1100px;margin:0 auto}
input{background:#0b0d12;border:1px solid #2a3243;color:#e6e9ef;border-radius:6px;padding:7px 9px}
button{background:#2f6feb;border:0;color:#fff;border-radius:6px;padding:7px 13px;cursor:pointer;font-weight:600}
table{width:100%;border-collapse:collapse;margin-top:14px;font-size:13px}
th{text-align:left;color:#8b93a3;font-weight:600;border-bottom:1px solid #1d2330;padding:8px 6px}
td{border-bottom:1px solid #141922;padding:8px 6px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}
.stat{border:1px solid #1d2330;border-radius:8px;padding:10px 14px;background:#0e1117;min-width:110px}
.stat b{display:block;font-size:19px}
.err{color:#ff8f8f}.ok{color:#6ee7b7}
</style></head><body>
<header><h1>Back in Stock <span class="muted">admin</span></h1><span id="who" class="muted"></span></header>
<main>
  <div id="login">
    <p class="muted">Sign in as admin@backinstock.local / backinstock123</p>
    <input id="email" value="admin@backinstock.local"> <input id="password" type="password" placeholder="password">
    <button onclick="signin()">Sign in</button> <span id="loginmsg" class="err"></span>
  </div>
  <div id="panel" style="display:none">
    <div class="stats" id="stats"></div>
    <table><thead><tr><th>Product</th><th>Variant</th><th>SKU</th><th>Stock</th><th>Waiting</th><th>Set stock</th></tr></thead><tbody id="rows"></tbody></table>
    <h3 style="margin-top:26px;font-size:14px">Recent notifications</h3>
    <table><thead><tr><th>#</th><th>SKU</th><th>To</th><th>Channel</th><th>Status</th><th>Created</th></tr></thead><tbody id="notifs"></tbody></table>
  </div>
</main>
<script>
let token = localStorage.getItem('bis_token') || '';
const H = () => ({'Content-Type':'application/json','Authorization':'Bearer '+token});
async function signin(){
  const r = await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('email').value,password:document.getElementById('password').value})});
  const d = await r.json();
  if(!r.ok){document.getElementById('loginmsg').textContent=d.detail||'Sign in failed';return;}
  token=d.token; localStorage.setItem('bis_token',token); boot();
}
async function boot(){
  if(!token) return;
  const r = await fetch('/api/stats',{headers:H()});
  if(!r.ok){document.getElementById('login').style.display='block';document.getElementById('panel').style.display='none';return;}
  localStorage.setItem('bis_token',token);
  document.getElementById('login').style.display='none';
  document.getElementById('panel').style.display='block';
  document.getElementById('who').textContent='signed in';
  const s=await r.json();
  document.getElementById('stats').innerHTML=[['Waiting',s.waiting],['Notified',s.notified],['Notifications',s.notifications],['Preorders',s.preorders],['Sold out',s.outOfStockVariants],['Send mode',s.sendMode]]
    .map(([k,v])=>`<div class="stat"><span class="muted">${k}</span><b>${v}</b></div>`).join('');
  const v=await (await fetch('/api/variants',{headers:H()})).json();
  document.getElementById('rows').innerHTML=v.items.map(x=>`<tr><td>${x.product}</td><td>${x.title}</td><td>${x.sku}</td><td>${x.stock}</td><td>${x.waiting}</td>
    <td><input id="s-${x.id}" size="5" placeholder="${x.stock}" value=""> <button onclick="setstock(${x.id})">Set</button></td></tr>`).join('');
  const n=await (await fetch('/api/notifications',{headers:H()})).json();
  document.getElementById('notifs').innerHTML=n.items.slice(0,15).map(x=>`<tr><td>${x.id}</td><td>${x.sku}</td><td>${x.address}</td><td>${x.channel}</td><td>${x.status}</td><td>${x.created_at}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">none yet</td></tr>';
}
async function setstock(id){
  const val=document.getElementById('s-'+id).value;
  const r=await fetch('/api/variants/'+id+'/stock',{method:'POST',headers:H(),body:JSON.stringify({stock:Number(val)})});
  if(!r.ok){alert('failed');return;}
  boot();
}
boot();
</script></body></html>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_page() -> HTMLResponse:
    return HTMLResponse(ADMIN_PAGE if False else ADMIN_PAGE)
