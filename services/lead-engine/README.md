# Lead Engine

Self-hosted prospect search and contact-enrichment workspace inspired by SalesQL.
Search contacts, reveal work email candidates, enrich LinkedIn URLs and CSVs using provider APIs,
verify addresses, organize lists, run tracked **simulated** email campaigns, and connect agents over MCP.

> **Privacy/data-source line:** This engine does not scrape LinkedIn or bypass its login, anti-bot
> controls, or rate limits. Seeded records are fictional demo data. Real personal emails and phone
> numbers are only returned from a provider API you configure; the engine never fabricates them.
> Campaign sending is simulated by default and sends no email unless you explicitly configure SMTP.

## Quick start

```bash
git clone https://github.com/venugopal1902/replicas.git
cd replicas/services/lead-engine
uv sync --python 3.12
uv run python scripts/run_server.py
```

Open **http://localhost:8500**.

Development login: `admin@leadengine.local` / `leadengine123`.
Change the credentials and secret settings before using it beyond a local development machine.

## How to check if it is working

The server should be running for Level 1, Level 3 and the live feature suite.

### 1. Health check

```bash
curl http://localhost:8500/health
```

Expected: `{"status":"online","service":"Lead Engine",...}`.

### 2. Automated tests

Run these from the service directory:

```bash
uv run python tests/test_enrichment.py  # parsers, email patterns, DNS/MX, domain lookup
uv run python tests/test_campaigns.py   # campaign CRUD/quota/steps/tracking simulation
uv run python tests/test_lead_engine.py # live API feature suite; requires server on :8500
```

The UI has a browser-based smoke test. It drives local Chrome over CDP (a cloud browser cannot reach
localhost):

```bash
chrome --headless=new --remote-debugging-port=0 --user-data-dir=./.tmp-chrome about:blank
PORT=$(head -1 ./.tmp-chrome/DevToolsActivePort)
CDP_URL=http://127.0.0.1:$PORT node tests/ui_smoke.mjs
```

Expected: `20/20 UI checks passed`.

## What works

- **Prospector:** seeded contacts, one free-text box (matches name, company, title, domain, location or LinkedIn slug), structured filters, pagination. Search costs no credits.
- **Reveal:** generates and checks work-email candidates using a name/domain. One credit only when an email is returned; repeat reveals cost zero.
- **Provider enrichment:** optional Hunter, Apollo and People Data Labs keys, stored per workspace in the UI. Provider results can include personal email and phone.
- **LinkedIn URLs:** a company name or domain is **required** alongside the URL. A LinkedIn slug carries no company domain, so without one there is no pattern to verify and the row reports `needs company or domain`.
- **CSV:** URL, lead identity and reverse-email modes; poll jobs and download output. Every job now exposes per-row outcomes at `GET /api/jobs/{id}/rows`.
- **Verifier:** syntax, role-account and MX checks; optional SMTP probe is off by default. Verifier credits are separate.
- **Lists:** create folders with a colour, add/remove contacts, see counts. The colour renders as a swatch in the folder row.
- **Campaigns:** sequences up to 10 steps, recipients and open/click tracking. **Send is simulation-only by default.**
- **API and MCP:** workspace API keys and six MCP tools (`search_prospects`, `reveal_contact`, `enrich_linkedin`, `verify_email`, `list_folders`, `check_credits`).
- **Login/workspaces:** PBKDF2 password hashes, sessions, scoped data, quota meters.

## Email enrichment: important distinction

A LinkedIn URL by itself does not contain the person's email. If you configure no external provider,
Lead Engine can:

1. Parse the public URL slug as a name hint.
2. Resolve a company domain from a supplied company/domain.
3. Generate ranked **work-email candidates** (e.g. `first.last@company-domain`).
4. Check the domain's MX records and optionally perform SMTP RCPT probing if you explicitly enable it.

An MX record only proves the domain accepts mail, not that a mailbox exists. Therefore MX-only
candidates are labeled **risky**, not verified. To get genuinely provider-backed personal email or
phone data, configure your own account/API key. Never treat a generated candidate as a confirmed address.

### Provider configuration

Two ways, both supported. Keys saved in the UI win over `.env`.

**In the app (recommended):** Settings → Data providers → paste Hunter / Apollo / PDL keys → Save keys →
Test connection. Keys are stored on the workspace row and read back masked (`••••1234`), never in full.

**In `.env`:** used as the fallback when the workspace has no key.

```dotenv
HUNTER_API_KEY=
APOLLO_API_KEY=
PDL_API_KEY=
```

The engine calls providers only when a key is present. It does not scrape LinkedIn pages.

### Resetting demo data

Automated tests and manual probing run against the same SQLite file the app serves, so they can leave
duplicate contacts and stray folders behind. To get back to a clean, seeded demo state:

```bash
# stop the server first, Windows holds the file open
uv run python scripts/reset_demo_data.py --force
```

This deletes `data/lead_engine.db`, reseeds 12 companies and 48 contacts on real mail-capable domains,
and restores the default login. Then set your own password with `scripts/set_password.py`.

If demo reveals return nothing, the usual cause is a company domain with no MX record. Seeded domains
are all MX-capable; check a custom one with `uv run python -c "import asyncio;from lead_engine.enrichment
import mx_lookup;print(asyncio.run(mx_lookup('example.com')))"`.

### SMTP probing

Off by default. Some servers block probes or accept every recipient, so an SMTP result is not perfect proof.
Enable only if appropriate for your use and respect your network/provider policies:

```dotenv
SMTP_PROBE=true
SMTP_FROM=verify@your-domain.example
```

## API and MCP

- REST: `http://localhost:8500/api`
- Swagger: `http://localhost:8500/docs`
- MCP: `http://localhost:8500/mcp`
- Health: `http://localhost:8500/health`
- Auth: browser bearer session token, or `x-api-key` for machine clients

See [docs/api-contract.md](docs/api-contract.md) for the complete route contract.

### MCP client snippet

```json
{
  "mcpServers": {
    "lead-engine": {
      "url": "http://localhost:8500/mcp",
      "headers": { "Authorization": "Bearer <SESSION_OR_API_KEY>" }
    }
  }
}
```

## Troubleshooting sign-in

If the login card rejects you:

```bash
cd C:/Users/venug/Desktop/replicas/services/lead-engine

# 1. Is the engine up?
curl http://localhost:8500/health

# 2. Which accounts exist?
uv run python scripts/set_password.py --list

# 3. Set a known password
uv run python scripts/set_password.py --email admin@leadengine.local --password leadengine123

# 4. Prove the credentials work without a browser
curl -X POST http://localhost:8500/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@leadengine.local","password":"leadengine123"}'
```

A `200` with a `token` means the backend is fine and the problem is browser state: clear the site
data for `localhost:8500` (the app stores a session token in `localStorage`), or open a private
window. A `401` means the password is wrong — use step 3.

The login screen now prints the engine origin and the reset command, and distinguishes
"cannot reach the API" from "wrong email or password".

## Development/seed credentials

Fresh database seed:

- Email: `admin@leadengine.local`
- Password: `leadengine123`
- API key: `lke_live_demod...` (list it in API & MCP screen)

This is a development-only account. Do not use these credentials on an internet-exposed service.

## Campaign compliance and safety

Campaign sends default to simulation. No email is sent by the service unless you set `CAMPAIGN_SEND_MODE=smtp`
and provide SMTP configuration. Use only contact data you have a lawful basis to process, follow applicable
privacy and anti-spam law, and honor consent, opt-out and suppression requirements. Public open/click tracking
is enabled only for campaign test events within this installation.

## Known limits

- Contacts shipped in the seed database are fictional and for UI/test demonstrations only.
- Without Hunter/Apollo/PDL, work email is candidate-pattern + MX information, not guaranteed delivery.
- Personal email and phone are unavailable without provider results.
- LinkedIn profile scraping, login automation and anti-bot circumvention are intentionally not implemented.
- SMTP probing is best-effort and off by default.
- Campaign sending is simulated unless explicitly opted in.
- The current database defaults to SQLite; use PostgreSQL and migrations for multi-user production deployment.

## Verify installation summary

```bash
curl http://localhost:8500/health
uv run python tests/test_enrichment.py
uv run python tests/test_campaigns.py
uv run python tests/test_lead_engine.py
```
