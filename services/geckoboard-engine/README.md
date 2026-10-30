# Geckoboard Engine

A local, clean-room replica of the product's core data-to-screen workflow: define custom datasets, push typed records, compose dashboard widgets, and publish a TV rotation. This is a focused local implementation, not a claim of complete Geckoboard API or product parity.

## Run it

Requires Python 3.12 and `uv`.

```bash
uv sync --python 3.12
```

Before the first boot, set both `GECKOBOARD_ADMIN_PASSWORD` and `GECKOBOARD_DATASET_API_KEY` in the process environment or your preferred secret manager. The service has no baked-in credentials, prints no generated secrets, and fails with setup instructions if either first-boot value is missing. It stores the admin password as a PBKDF2 hash and the high-entropy dataset API key as a SHA-256 hash. Existing database rows are authoritative on later boots.

Then start the local service:

```bash
uv run python scripts/run_server.py
```

Admin console: `http://127.0.0.1:8640/`

Health: `http://127.0.0.1:8640/health`

The default host is loopback only. To seed sample support and revenue data, run `uv run python scripts/seed_demo.py` after the first boot. The seed is idempotent and creates two datasets, two dashboards, and a public TV rotation. The rotation link is shown in the authenticated TV Rotations view, not printed by the seed script.

## Verify

The acceptance suite starts and stops its own server, refuses to bind if the selected test port is already listening, uses a separate temporary database, and removes its test data when it exits. The test port is configurable from the first run; health assertions compare against that selected value.

PowerShell:

```powershell
$env:GECKOBOARD_TEST_PORT = "8641"
uv run pytest -q
```

Bash:

```bash
GECKOBOARD_TEST_PORT=8641 uv run pytest -q
```

To use the contract port, omit the override. Do not run the suite against a port owned by another service.

## Dataset API

The push API follows the core Datasets API pattern: Basic Auth with the API key as the username and an empty password; `PUT` defines a dataset, `POST` appends records, and `PUT` on `/data` replaces them. A dataset schema maps field names to types and can define `unique_by` keys. Repeated records with the same key update the existing row.

```bash
curl -u "$GECKOBOARD_DATASET_API_KEY:" \
  -H "Content-Type: application/json" \
  -X PUT http://127.0.0.1:8640/api/datasets/sales.by_day \
  -d '{"fields":{"date":{"type":"date","name":"Date","optional":false},"amount":{"type":"number","name":"Amount","optional":false}},"unique_by":["date"]}'

curl -u "$GECKOBOARD_DATASET_API_KEY:" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8640/api/datasets/sales.by_day/data \
  -d '{"data":[{"date":"2026-05-21","amount":42}]}'
```

Supported field types: `date`, `datetime`, `duration`, `money`, `number`, `percentage`, and `string`. Requests are capped at 2 MiB and 1,000 records; each dataset stores at most 50,000 rows, and string values are limited to 5,000 characters. Raw-record reads support `?limit=...&offset=...` pagination. The API key can be rotated from the admin console; the new key is returned once in the authenticated response and is never logged. Rotating it immediately invalidates existing integrations.

### Main routes

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/health` | public | status, configured port, dataset/dashboard counts |
| POST | `/api/auth/login` | public | creates an HttpOnly admin session cookie |
| POST | `/api/auth/logout` | admin cookie | revokes the session |
| POST | `/api/admin/datasets` | admin cookie | create a dataset from its schema |
| GET | `/api/admin/datasets` | admin cookie | list schemas; never returns the API key |
| GET | `/api/admin/datasets/{id}/export.csv` | admin cookie | export stored records |
| PUT | `/api/datasets/{id}` | dataset API key | define or verify a dataset schema |
| POST | `/api/datasets/{id}/data` | dataset API key | append/upsert records |
| PUT | `/api/datasets/{id}/data` | dataset API key | replace all records |
| GET | `/api/datasets/{id}/data` | dataset API key | read raw records |
| POST/GET/PUT | `/api/admin/dashboards` | admin cookie | create, list, and update widget compositions |
| GET | `/api/admin/dashboards/{id}/preview` | admin cookie | compute widgets from stored dataset rows |
| POST/GET/PUT | `/api/admin/rotations` | admin cookie | create, list, and update TV rotations |
| GET | `/api/public/rotations/{public_id}` | public link | read only the dashboards selected for an active rotation |
| GET | `/tv/{public_id}` | public link | full-screen TV mode with timed dashboard rotation |

Supported widgets: number, line, column, bar, leaderboard, and table. A line widget needs a date or datetime field; other chart widgets validate their metric and grouping fields against the dataset schema. Widgets use one dataset each.

## Security notes and honest limits

- Admin sessions are HttpOnly, SameSite=Lax cookies; they are not returned as JavaScript-readable bearer tokens.
- Admin credentials and the Datasets API key are separate. Admin cookies do not authorize dataset pushes.
- TV rotation links are public by design. Only include data intended for anyone with the link.
- The default bind address is `127.0.0.1`. Put a real deployment behind TLS and set `GECKOBOARD_COOKIE_SECURE=true` before exposing it.
- This replica does not connect to Geckoboard, external data sources, billing, or Geckoboard's hosted widgets. Commercial pricing, quotas, and undocumented product behavior are intentionally not asserted.

## Sources

- Geckoboard Datasets API reference: https://developer.geckoboard.com/ . The implementation follows the documented dataset definition, Basic Auth, append/replace, field typing, and unique-key concepts, with a deliberately smaller local surface.
- FastAPI lifespan events: https://fastapi.tiangolo.com/advanced/events/ .
- FastAPI response cookies: https://fastapi.tiangolo.com/advanced/response-cookies/ .
- FastAPI HTTP Basic Auth: https://fastapi.tiangolo.com/advanced/security/http-basic-auth/ .
- FastAPI query validation: https://fastapi.tiangolo.com/tutorial/query-params-str-validations/ .
