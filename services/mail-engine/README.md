# Mail Engine

Self-hosted cold email engine inspired by Instantly and Lemlist: campaigns, multi-step
sequences, multiple mailboxes with warmup simulation, suppression lists, a threaded inbox,
per-campaign analytics and an MCP bridge for agents.

> **Sending is simulated by default.** With `MAIL_SEND_MODE=simulate` (the default) every send
> writes a row to SQLite and nothing leaves the machine: no SMTP connection, no external API.
> Seeded leads are fictional people on fictional domains. Only switch to `smtp` mode with your
> own server and your own list, and only after checking the law that applies to you (GDPR/PECR,
> CAN-SPAM). The unsubscribe link and suppression list are enforced in code.

Port: **8640** (8600 back-in-stock, 8610 churn-buster, 8620 bannerbear-engine, 8630 zigpoll-engine).

## Quick start

```bash
cd replicas/services/mail-engine
uv sync --python 3.12
uv run python scripts/run_server.py
```

Open **http://localhost:8640**.

Development login: `admin@mailengine.local` / `mailengine123`.
Change the credentials and `MAIL_UNSUB_SECRET` before using this beyond a local machine.

Reseed the demo workspace at any time:

```bash
uv run python scripts/reset_demo.py
```

## How to check if it is working

### 1. Health

```bash
curl http://localhost:8640/health
```

Expected: `{"status":"online","service":"Mail Engine",...,"sendMode":"simulate"}`.

### 2. Automated suites

Server must be running for the live suite. From the service directory:

```bash
uv run python tests/test_units.py         # merge tags, HTML escaping, tokens, PBKDF2, subject score
uv run python tests/test_mail_engine.py   # live API: auth, CRUD, enroll, send, tracking, inbox, analytics
```

Expected: `24/24 unit checks passed` and `18/18 live checks passed`.

### 3. UI smoke test

Chrome must be started with a debugging port (it needs to reach localhost, so a cloud browser
will not work):

```bash
chrome --headless=new --remote-debugging-port=9333 --user-data-dir=./.tmp-chrome about:blank
CDP_URL=http://127.0.0.1:9333 node tests/ui_smoke.mjs
```

Expected: `15/15 UI checks passed` (login, dashboard, campaign detail preview, leads search,
inbox, mailboxes, settings, zero JS exceptions, zero browser 5xx).

Verified totals for this build: **24 unit + 18 live + 15 UI = 57 checks**.

## What works

- **Auth** — PBKDF2-SHA256 (240k rounds) passwords, session tokens as bearer or `mail_session`
  cookie, unauthenticated requests rejected with 401.
- **Mailboxes** — per-mailbox daily limits, pause/activate, warmup score, remaining quota.
- **Campaigns** — draft/active/paused, per-campaign daily cap, stop-on-reply flag, tracking toggle,
  activate and pause endpoints.
- **Sequences** — ordered steps with day delays, editable subject/body, merge tags
  (`{{first_name}}`, `{{company}}`, `{{title}}`, ...), unknown tags reported instead of silently dropped.
- **Preview** — server-rendered subject, plain text and HTML for a real lead, plus a subject
  quality score that flags all-caps, overlong subjects and spam words.
- **Leads** — CRUD, search, CSV import with header aliases, duplicate and suppression rejection.
- **Enrollment** — enroll selected leads or every eligible lead; suppressed and already-enrolled
  leads are skipped and reported.
- **Sending** — `send-next` sends the next due step for each active enrollment, respects campaign
  and mailbox daily caps, renders merge tags per lead, writes a Send row with a tracking id,
  advances the enrollment and finishes it after the last step. Simulated: no network traffic.
- **Suppressions** — block at send time, with the block reason counted in the API response.
- **Tracking** — 1x1 open pixel, click redirect, unsubscribe link with an HMAC token that writes
  a suppression and pauses enrollments.
- **Inbox** — threaded replies, classification chips, read/unread, reply writes an outbound send
  and flips the lead to `replied`.
- **Analytics** — totals, per-campaign rates, 7-day daily series, inbox breakdown.
- **MCP bridge** — `GET /mcp/tools` and `POST /mcp/call` for 8 tools so other agents can drive it.

## Layout

```
mail-engine/
  mail_engine/
    main.py          FastAPI app, /health, static mount
    config.py        settings (port 8640, send mode, unsubscribe secret)
    models.py        SQLAlchemy 2 models
    database.py      async engine + seeded demo dataset
    auth.py          PBKDF2, sessions, request context
    render.py        merge tags, HTML build, tracking, subject score
    serializers.py   camelCase JSON shapes
    routes/          auth, workspace, campaigns, leads, sending, inbox, warmup, analytics, mcp
  static/            index.html, styles.css, app.js (no build step)
  tests/             test_units.py, test_mail_engine.py, ui_smoke.mjs
  scripts/           run_server.py, reset_demo.py
  docs/api-contract.md
```

## Verified totals in the sprint

`SPRINT.md` at the repo root records the literal pass counts for each tick. This service passed
`24/24` unit, `18/18` live API and `15/15` UI checks on 2026-10-30.
