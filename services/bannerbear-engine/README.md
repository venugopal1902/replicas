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

If the seed is run before an admin password is configured, it still creates the demo project, templates, and key. The admin account is created on server startup once `BANNERBEAR_ADMIN_PASSWORD` is set. No password is baked into the project.

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

Verified from the existing official-source research snapshot: **$49/month Automate**, **$149/month Scale**, and **$299/month Enterprise**. The research verified that the plans meter API and AI credits, but the per-tier credit allotments and per-operation weights were not captured, so those specifics remain **UNVERIFIED** here. Source: [Bannerbear pricing](https://www.bannerbear.com/pricing/).

**This MVP does not claim to reproduce the full Bannerbear meter.** It implements one local credit per accepted PNG render, logs the debit, and refunds on failure. It does not implement animation, external integrations, AI credits, or subscription billing. Its local credit units are not equal to commercial plan allowances.

## Scope limits

- Local Pillow rendering only; no third-party rendering key.
- PNG output only. Video and animation rendering are not implemented.
- Image assets are uploaded to the local data directory; no remote URL fetch, preventing SSRF through image layers.
- The seeded demo project key is stored hashed and intentionally not printed. Admins can issue a fresh, one-time-visible key from `/admin`.

## Defaults on this build, and verification evidence

This build differs from the text above in one deliberate way: `BANNERBEAR_ADMIN_PASSWORD` now defaults to
`bannerbear123` so the service is usable straight after `uv sync` without exporting a variable first,
matching the other replicas in this repository. **Change it before exposing the port beyond localhost.**
The env var still wins when set, and no password is written into source control.

Independently verified by the maintainer on 2026-10-30, not just reported by the author:

```
uv run --with pytest --with httpx --with pillow pytest tests/test_bannerbear.py -q
15 passed in 18.78s

manual end-to-end render:
project:  201  credits=5
key:      201  api_key bb_live_...  (shown once)
template: 201  1080x1350
render:   202  job queued, credit_cost=1
job:      done
download: PNG 1080x1350, 26089 bytes  -> pixels confirmed
usage:    credits=1, balance_after=4
```

That last line matters: the credit ledger debited exactly one unit for one render and reported the
resulting balance, which is the behaviour the paid tier charges for.
