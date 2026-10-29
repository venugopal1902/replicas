# Blotato Engine

A self-hosted, Blotato-compatible social media API, MCP server and web console.
Runs on your machine with one command. No subscription, no post caps, no per-seat billing.

- **38 REST endpoints** · **17 MCP tools** · **9 platforms** · **9-screen web console**
- Python 3.12 · FastAPI · SQLite · APScheduler · Pillow
- Runtime: `http://localhost:8400`

---

## Quick start

```bash
git clone https://github.com/venugopal1902/replicas.git
cd replicas/services/blotato-engine

uv sync --python 3.12          # install dependencies
uv run python scripts/run_server.py
```

Open **http://localhost:8400** and paste the master key:

```
blt_master_venu_secret_2026
```

That key is unlimited. It is printed by `scripts/run_server.py` on boot and defined in
`blotato_engine/config.py` as `MASTER_API_KEY`.

---

## Is it working? Check in three levels.

### Level 1 — ten seconds, one command

```bash
curl http://localhost:8400/health
```

Expected:

```json
{"status":"online","service":"Blotato Self-Hosted Engine","version":"2.0.0",
 "mcpEndpoint":"/mcp","restEndpoint":"/v2","ui":"/"}
```

If that fails, the server is not running. Start it and check the terminal for a traceback.

### Level 2 — the automated proof

Two suites. Both must exit `0`.

```bash
uv run python tests/test_engine.py      # 52 checks, in-process, no server needed
uv run python tests/test_features.py    # 13 groups, hits the live server on :8400
```

`test_engine.py` boots the ASGI app against a fresh database and exercises every endpoint
group: accounts, publish, schedule, media, inbox, automations, visuals, sources, analytics,
MCP and auth.

`test_features.py` requires the server to be running. It proves the things a unit test cannot:

| It checks | What proves it |
| --- | --- |
| Media upload round-trip | PUT bytes, GET the public URL, compare payloads |
| Every visual template | 6 templates render PNGs larger than 5 KB |
| The scheduler actually fires | post scheduled 7s ahead flips to `published` on its own |
| Automations execute | keyword hit produces a comment reply and a DM |
| Gates apply | follow-gate and email-gate text lands in the DM body |
| Credit metering | 2-credit key publishes twice, third call returns 403 |
| All 17 MCP tools | each tool called and its result inspected |
| Source extraction | a real URL is fetched and repurposed into drafts |

Expected tail:

```
==============================================================
PASSED 52   FAILED 0
==============================================================
FEATURES VERIFIED 13/13
```

### Level 3 — click through the console

Open http://localhost:8400 and walk the sidebar. Each screen should show real data, not an error:

| Screen | Working means |
| --- | --- |
| Dashboard | plan + credits, 9 account cards, recent posts table |
| Compose | type text, pick accounts, Publish returns a status per account |
| Queue | scheduled posts listed; slot manager adds and deletes slots |
| Inbox | comments listed, reply saves, DM threads open and send |
| Automations | create one, hit **Test**, see matched actions |
| Visuals | pick a template, Generate, images appear |
| Sources | paste a URL, Extract, Repurpose produces drafts |
| Analytics | totals, metric selector, ranked bars, post drill-down |
| Settings | key change, account list, add account form |

If a screen is blank where data should be, open the browser devtools console — the app surfaces
every failure as a toast and logs the underlying error.

---

## Common failures and what they mean

| Symptom | Cause | Fix |
| --- | --- | --- |
| `401` on every call | key missing or wrong | send `blotato-api-key` header; use the master key above |
| `403 Insufficient credits` | client key exhausted | top it up, or use an admin key |
| Post status `failed`, error mentions OAuth or permissions | real credentials present but rejected | the engine does not fake publishes; fix the token or the app review |
| `status: published, simulated: true` | the account is configured with `provider: mock` | expected in demo; add real credentials for live posting |
| `uv sync` errors about Python version | wrong interpreter | `uv python pin 3.12` then `uv sync` |
| Port 8400 busy | another instance running | stop it, or set `PORT` in `.env` |
| Empty console after paste key | wrong key saved | click **Sign out** in the sidebar, paste again |

### Live vs simulated publishing

Accounts seeded for demo carry `"provider": "mock"` and publish through a deterministic
simulator. Those posts return `"simulated": true`.

When you attach real credentials, the engine uses the real platform connector. If the token is
wrong or lacks scope, the post **fails** and the platform's own error is returned. It will never
report a fake success. That distinction is deliberate and covered by the test suite.

---

## Configuration

Environment variables, or a `.env` next to `pyproject.toml`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8400` | HTTP port |
| `HOST` | `0.0.0.0` | bind address |
| `BLOTATO_MASTER_KEY` | `blt_master_venu_secret_2026` | admin key, change before exposing |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/blotato.db` | swap for Postgres in production |
| `META_ACCESS_TOKEN` | — | Meta Graph default |
| `TWITTER_BEARER_TOKEN` | — | X API v2 default |
| `LINKEDIN_ACCESS_TOKEN` | — | LinkedIn default |

Platform credentials normally live per account (Settings → add account), stored as JSON in the
`social_accounts` table.

---

## Adding a real platform account

```bash
curl -X POST http://localhost:8400/v2/users/me/accounts \
  -H "blotato-api-key: blt_master_venu_secret_2026" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "instagram",
    "name": "My IG",
    "username": "myhandle",
    "credentials": {"access_token": "...", "ig_user_id": "1784..."}
  }'
```

Required credentials per platform:

| Platform | Needs |
| --- | --- |
| instagram / facebook | `access_token` + `ig_user_id` or `page_id` |
| twitter / x | `access_token` (OAuth 2 user token, write scope) |
| linkedin | `access_token` + `author_urn` |
| bluesky | `identifier` + app `password` |
| youtube | `access_token` with upload scope |
| threads | `access_token` + `user_id` |
| pinterest | `access_token` + `board_id` |
| tiktok | `access_token` with content posting scope |

---

## API map

Base: `http://localhost:8400/v2` · Auth header: `blotato-api-key`
Interactive docs: **http://localhost:8400/docs**

| Area | Endpoints |
| --- | --- |
| Accounts | `GET/POST /users/me/accounts` |
| Publishing | `POST /posts`, `GET /posts`, `GET /posts/{id}` |
| Scheduling | `GET /schedules`, `PATCH/DELETE /schedules/{id}`, `GET/POST /schedule-slots`, `DELETE /schedule-slots/{id}`, `GET /schedule-slots/next` |
| Media | `POST /media`, `PUT /media/{id}/raw`, `POST /media/upload`, `GET /media` |
| Comments | `GET /comments`, `POST /comments/{id}/reply`, `POST /comments/{id}/read` |
| Messages | `GET /messages`, `GET /messages/{threadId}`, `POST /messages` |
| Automations | `GET/POST /dm-automations`, `PATCH/DELETE /dm-automations/{id}`, `POST /dm-automations/{id}/test`, `GET /webhook-events` |
| Webhooks | `POST /webhooks/meta`, `POST /webhooks/test` |
| Visuals | `GET /videos/templates`, `POST /videos/from-templates`, `GET /videos/creations`, `GET /videos/creations/{id}`, `DELETE /videos/{id}` |
| Sources | `POST/GET /source-resolutions`, `GET /source-resolutions/{id}`, `POST /source-resolutions/{id}/repurpose` |
| Analytics | `GET /analytics`, `GET /analytics/top`, `POST /analytics/snapshot` |
| Billing | `GET /credits`, `POST /admin/keys` |

---

## MCP for AI agents

```json
{
  "mcpServers": {
    "blotato": {
      "url": "http://localhost:8400/mcp",
      "headers": { "blotato-api-key": "blt_master_venu_secret_2026" }
    }
  }
}
```

17 tools: accounts, publish, post status, schedules, next slot, analytics, top posts, comments,
reply, threads, thread read, send message, automations, run automation, templates, create visual,
credits.

Quick check without a client:

```bash
curl -s http://localhost:8400/mcp \
  -H "blotato-api-key: blt_master_venu_secret_2026" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -c 300
```

---

## Selling access

Every key carries a plan, a credit balance and an hourly ceiling. Writes are refused once the
balance hits zero.

```bash
uv run python scripts/create_api_key.py --client "Acme Agency" --plan starter --credits 1000
```

It prints the client's setup card: header name, REST base, MCP endpoint and key.

| Tier | Price | Limits |
| --- | --- | --- |
| Personal | $0 | master key, unlimited |
| Starter | $19/mo | 1,000 credits, 1,800 req/hr |
| Pro | $49/mo | 5,000 credits, 3,600 req/hr |
| Agency | $99/mo | unlimited, 10,000 req/hr |

Before exposing it publicly: change `MASTER_API_KEY`, put it behind Caddy or Nginx with TLS, and
never bind port 8400 straight to the internet.

---

## Project structure

```
blotato-engine/
├── blotato_engine/
│   ├── config.py            settings and paths
│   ├── database.py          async engine, seeding, light migrations
│   ├── models.py            tables and schemas
│   ├── auth.py              api-key auth and credit gate
│   ├── scheduler.py         due-post dispatcher (15s)
│   ├── visuals_render.py    Pillow templates
│   ├── connectors/          meta, twitter, linkedin, bluesky,
│   │                        youtube, threads, pinterest, tiktok, mock
│   ├── api/                 accounts, posts, schedules, media, inbox,
│   │                        automations, visuals, sources, analytics, billing
│   ├── mcp/server.py        JSON-RPC MCP, 17 tools
│   └── main.py              FastAPI app, routers, static mount
├── static/                  web console (index.html, styles.css, app.js)
├── scripts/                 run_server.py, create_api_key.py
├── tests/                   test_engine.py, test_features.py, ui_smoke.mjs
└── data/                    sqlite database + uploaded/generated media
```

---

## Web console verification (maintainers)

The console has its own headless test. The harness browser is cloud-hosted and cannot see
localhost, so drive a local Chrome instead:

```bash
chrome --headless=new --remote-debugging-port=0 --user-data-dir=./.tmp-chrome about:blank
PORT=$(head -1 ./.tmp-chrome/DevToolsActivePort)
CDP_URL=http://127.0.0.1:$PORT node tests/ui_smoke.mjs
```

Expected: `17/17 UI checks passed`.

---

## License

MIT. Independent implementation — not affiliated with or endorsed by Blotato or any platform
named in this repository.
