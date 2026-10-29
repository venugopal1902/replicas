# Bannerbear Engine

A clean-room, self-hosted template renderer with project-scoped bearer keys, async render jobs, real PNG output, and credit-ledger metering. It uses FastAPI, SQLAlchemy 2 async, SQLite/aiosqlite, PBKDF2 admin passwords, and Pillow. No third-party rendering key is required.

## Run on Windows

Requires Python 3.12 and `uv`.

```bash
uv sync --python 3.12
export BANNERBEAR_ADMIN_PASSWORD='choose-a-private-password'
uv run python scripts/seed_demo.py
uv run python scripts/run_server.py
```

Open `http://127.0.0.1:8620/admin`. The seeded admin email defaults to `admin@bannerbear.local`; the password comes only from `BANNERBEAR_ADMIN_PASSWORD`. The seed creates two templates, a demo project with credits, and one random project key stored as a hash. The seed never prints a key. In the console, issue a fresh project-scoped key; it is returned once and held in memory in that browser tab.

The server binds to `127.0.0.1:8620` by default. Override with `BANNERBEAR_HOST`, `BANNERBEAR_PORT`, `BANNERBEAR_DATABASE_URL`, and `BANNERBEAR_DATA_DIR` as needed. Keep the admin password private and put the service behind HTTPS before exposing it beyond localhost.

## Verify

```bash
uv run pytest -q -s
```

The acceptance suite exercises health, admin login, auth rejection, project/key creation, template creation, asynchronous render, status polling, PNG signature and dimensions, exact credit debit, usage row, zero-credit 402, admin HTML, image upload/compositing, and HTTP 5xx absence.

Health: `GET /health`  
Admin UI: `GET /admin`  
OpenAPI: `GET /docs`

## API outline

All project API endpoints require `Authorization: Bearer <project-key>`.

- `POST /api/assets`, multipart image upload (PNG, JPEG, WebP; maximum 5 MiB)
- `POST /api/templates`, stores JSON template definitions with named `text` or `image` layers
- `GET /api/templates` and `GET /api/templates/{template_id}`
- `POST /api/renders`, returns HTTP 202 and a job ID; each accepted PNG render reserves one project credit
- `GET /api/renders/{job_id}`, poll `queued`, `rendering`, `done`, or `failed`
- `GET /api/renders/{job_id}/download`, download the actual PNG after completion
- `GET /api/projects/me` and `GET /api/projects/me/usage`

Text layers accept `name`, `type: "text"`, `text`, `font_size`, `color` (`#RRGGBB`), `x`, and `y`. Image layers accept `name`, `type: "image"`, a project-owned `asset_id`, `x`, `y`, `w`, and `h`. Render overrides are keyed by layer name and validated before queueing. Assets cannot be referenced across projects. Failed renders are marked failed and the reserved credit is refunded with a compensating usage row.

Admin login: `POST /api/auth/login` with email/password returns an expiring bearer token. Admin-only APIs create projects and issue project-scoped keys. API-key secrets are hashed in storage and shown only once at issuance.

Example (replace the placeholder locally, never commit a real key):

```bash
curl -H "Authorization: Bearer $BB_API_KEY" http://127.0.0.1:8620/api/projects/me
```

## Credit model and vendor tiers

Verified Bannerbear list pricing is **$49/month Automate**, **$149/month Scale**, and **$299/month Enterprise**. The published plans meter API credits and AI credits: Automate includes 1,000 API credits and 200 AI credits; Scale includes 10,000 API credits and 600 AI credits; Enterprise includes 50,000 API credits and 1,250 AI credits. Higher plans add team/security features; Enterprise includes SSO, audit logs, zero data retention, and priority support. See [Bannerbear pricing](https://www.bannerbear.com/pricing/).

Published usage rules describe one image render as one API credit (formats and scale can multiply it); animation is charged at two credits per output second, rounded up, with a one-credit minimum. Tools are one to four credits depending on the operation; AI features add one AI credit per applicable layer. A workflow costs the sum of its steps.

**This MVP does not claim to reproduce the full Bannerbear meter.** It implements one local credit per accepted PNG render, logs the debit, and refunds on failure. It does not implement animation, external integrations, AI credits, or subscription billing. Its local credit units are not equal to commercial plan allowances.

## Scope limits

- Local Pillow rendering only; no third-party rendering key.
- PNG output only. Video and animation rendering are not implemented.
- Image assets are uploaded to the local data directory; no remote URL fetch, preventing SSRF through image layers.
- The seeded demo project key is stored hashed and intentionally not printed. Admins can issue a fresh, one-time-visible key from `/admin`.
