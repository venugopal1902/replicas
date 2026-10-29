# Zigpoll Engine

A local-first survey and poll replica. It includes typed questions, public response links, response storage, per-survey aggregates, CSV export, and HMAC-SHA256 signed response events. The $29 Zigpoll entry point and any tier ladder are **UNVERIFIED**; this project does not claim to reproduce commercial pricing.

## Install

Requires Python 3.12 and `uv`.

```bash
cd C:/Users/venug/Desktop/replicas/services/zigpoll-engine
uv sync --python 3.12
```

## Configure admin access

There is no baked-in password and no generated password is printed or logged. Before the first start, set `ZIGPOLL_ADMIN_PASSWORD` in the process environment or a private `.env` file. The app stores only a PBKDF2 hash. If no admin user exists and the setting is absent, startup fails with a clear setup error. Do not commit `.env`.

```bash
export ZIGPOLL_ADMIN_PASSWORD='choose-a-long-unique-password'
```

Optional settings: `ZIGPOLL_ADMIN_EMAIL`, `ZIGPOLL_ADMIN_NAME`, `ZIGPOLL_DATABASE_URL`, `ZIGPOLL_DATA_DIR`, `ZIGPOLL_HOST`, `ZIGPOLL_PORT` (default 8630).

## Run

```bash
uv run python scripts/run_server.py
```

- Admin console: http://127.0.0.1:8630/
- API docs: http://127.0.0.1:8630/docs
- Health: http://127.0.0.1:8630/health
- Public embeds: http://127.0.0.1:8630/s/{public_id}

## Seed demo data

Set the admin password as above, then run:

```bash
uv run python scripts/seed_demo.py
```

The seed creates two surveys and three example responses. It prints public survey IDs only, never the admin password or webhook secret.

## Verify

```bash
uv run pytest -q -s
uv run ruff check zigpoll_engine scripts tests
```

The acceptance suite starts and stops its own test-marked server on port 8630. It refuses to proceed if that port is occupied by another service. It tests admin authentication, public unauthenticated responses, type-specific validation, update/list, aggregates against raw response rows, CSV row counts, webhook HMAC acceptance and rejection, and zero HTTP 5xx responses.

## API overview

- `POST /api/auth/login`: admin email/password to short-lived bearer token.
- `POST /api/admin/surveys`, `PATCH /api/admin/surveys/{id}`, `GET /api/admin/surveys`: authenticated survey CRUD.
- `GET /api/public/{public_id}` and `POST /api/public/{public_id}/responses`: public survey and response routes. Public IDs are random and separate from the admin credential.
- `GET /api/admin/surveys/{id}/responses`, `/aggregates`, `/export.csv`: authenticated response review and export.
- `GET /api/admin/surveys/{id}/webhook-events`: signed response event outbox and delivery status. If an HTTPS webhook URL is configured, response submission posts the exact event body with `X-Zigpoll-Signature: sha256=<hex>` and `X-Zigpoll-Event: response.submitted`; redirects are disabled and delivery uses an eight-second timeout. The HMAC-SHA256 key is the per-survey secret.
- `POST /api/webhooks/test/{public_id}`: receiver-side signature verification test. Sign the exact raw request bytes with that survey's one-time webhook secret and send `X-Zigpoll-Signature: sha256=<hex>`. Invalid signatures receive 401.

Webhook URLs must use HTTPS. Webhook secrets are returned once when an admin creates a survey. Copy them into the intended consumer; they are not included in later survey-list responses. Protect the local SQLite data directory because it contains these signing secrets and response data.

## Supported question types

- `single_choice`: exactly one configured option.
- `multi_choice`: a unique list of configured options.
- `rating`: integer within the configured inclusive range.
- `free_text`: text up to 4,000 characters.

Example question JSON:

```json
[
  {"id":"rating","label":"Rate the experience","type":"rating","min_value":1,"max_value":5,"required":true},
  {"id":"reason","label":"Pick one","type":"single_choice","options":["Quality","Speed"],"required":true}
]
```
