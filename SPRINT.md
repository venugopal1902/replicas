# 24-Hour Build Sprint

Owner: chief-bot (supervisor). Window: 24 hours from creation. Tick: every 10 minutes.

Rules for every tick:
- Read this file first. Continue from the ledger, never restart completed work.
- One owner per task. Spawn subagents for parallel work (`delegate_task`).
- A service only counts as DONE when it passes >= 10 real checks recorded below.
- Never publish to social media without explicit APPROVE from Venu.
- Never spend Google Flow / Higgsfield credits without a written request from Venu.
- Append results, do not rewrite history.
- Keep the final tick response under 400 characters.

## Service backlog

Status: TODO | BUILDING | VERIFY | DONE | BLOCKED

Reprioritised from RESEARCH-SERVICES.md (ranked by recurring customer value per solo-build hour).
Build order is top-down. Do not start a lower row while a higher row is TODO.

| # | Service | Replaces | Price anchor | Diff | Status | Checks | Notes |
|---|---|---|---|---|---|---|---|
| — | blotato-engine | Blotato | $29/mo | — | DONE | 52+13+18 | Social API, MCP, 9-platform console. Port 8400 |
| — | lead-engine | SalesQL | $99+/mo | — | DONE | 18 UI + API sweep | LinkedIn to work email, provider keys. Port 8500 |
| 1 | back-in-stock | Back in Stock by Amp | $19-99/mo | S/M | DONE | 20 | Restock waitlists, queued notifications, preorders, storefront + admin, CSV. Port 8600 |
| 2 | churn-buster | Churn Buster | $269+/mo | M | DONE | 21 | Dunning campaigns, Stripe-shaped webhook, recovery analytics, admin console, CSV. Port 8610 |
| 3 | bannerbear-engine | Bannerbear | $49-299/mo | M | DONE | 15 pytest + E2E | Templates, async PNG renders, project keys, credit metering with 402 on empty. Port 8620. Built by hermes, verified + documented by chief |
| 4 | zigpoll-engine | Zigpoll | $29+/mo | M | TODO | 0 | Survey builder, public embed, response store, exports, signed webhooks. Port 8630 |
| 5 | geckoboard-engine | Geckoboard | $79/319/mo | M | TODO | 0 | Custom datasets push API, widget composition, TV rotation mode. Port 8640 |
| 6 | baremetrics-engine | Baremetrics | UNVERIFIED | M/L | TODO | 0 | MRR/ARR metrics from Stripe, churn, forecasting. Port 8650 |
| 7 | smile-engine | Smile.io | $15/79/199/mo | L | TODO | 0 | Points ledger, referrals, VIP tiers. Defer: ledger correctness + fraud controls. Port 8660 |

Infrastructure primitives, not replacements. Build only if a ranked row needs them:

| Service | Note | Status |
|---|---|---|
| mail-engine | SMTP send + sequence engine. Reusable by back-in-stock and churn-buster. Not a product on its own. | BUILDING |
| schedule-engine | Calendly-shaped availability + ICS. No clear paid-tier moat. | TODO |
| meeting-engine | Transcript + action items. Keep as candidate. | TODO |
| tts-engine | Voice synthesis via provider keys. | TODO |

Research notes that constrain design:
- Back in Stock and Smile.io need Shopify app installation, not a pasted key. Build the API and a simulated merchant, plus a real adapter behind an OAuth flag.
- Churn Buster and Baremetrics need a billing-provider connection. Stripe-first, adapter behind a flag.
- Bannerbear needs no third-party key. That makes it the easiest one to demo end to end and to sell as a hosted API.
- Zigpoll: exact tier pricing is UNVERIFIED. Do not print a price ladder for it on the catalog site.
- Baremetrics: pricing UNVERIFIED. Do not state a price.

## Verification contract (every service, every tick)

Minimum 10 checks, all executed, all real:

1. Install from a clean venv succeeds.
2. Server boots and `/health` returns 200.
3. Auth: bad password rejected, good password returns a token.
4. Unauthenticated request is rejected.
5. Primary create endpoint writes a row.
6. Primary read endpoint returns that row.
7. Primary update endpoint persists the change.
8. Primary delete endpoint removes it.
9. One end-to-end business flow produces the expected artifact (file, email, event).
10. Web UI loads, signs in, and renders the main screen without JS exceptions.
11. No HTTP 5xx across the run.
12. OpenAPI route count matches the documented count (drift check).

Record the literal numbers. A check that was not executed is not a pass.

## Video lane (nandini-bot)

- Source trending/viral videos, pick 1 concept per cycle.
- Fresh generation only, never reuse the last live upload.
- Dialogue videos need lip-sync / facial motion. Still + audio is a REJECT.
- Identity lock: still_206bfa6a.png only.
- STOP at APPROVE gate. No Instagram/YouTube upload without Venu's explicit APPROVE on Telegram.

## Timing reality (measured, not guessed)

- A single subagent file read in this environment has taken 80-140 seconds.
- A comparable build task previously took 28 minutes of wall time end to end.
- Therefore a 10-minute tick CANNOT complete a service. Ticks supervise; parallel subagents build.
- Never spawn a second subagent for a service that already has one running. Check
  `delegate_task(action='list')` and the service folder mtime first.
- Steer a stalled child to write files immediately instead of reading more reference code.

## Ledger

Newest first. One line per tick: `HH:MM | action | evidence`

- T+00:45 | churn-buster BUILT AND VERIFIED by chief-bot | 21/21 checks passed live :8610, zero 5xx, Stripe webhook round trip
- T+00:44 | both delegated children confirmed dead | interrupted after 2 API calls each, 685s, zero files written
- T+00:28 | back-in-stock BUILT AND VERIFIED by chief-bot directly | 20/20 checks passed against live :8600, zero 5xx
- T+00:20 | subagents stopped after measuring 98-158s per tool call | 8 min of wall time produced 2 tool calls, zero files

- T+00:00 | sprint created, cron scheduled every 10 min for 24h | 144 ticks
- T+01:00 | backlog reprioritised from hermes research (7 ranked replacements, build order set) | RESEARCH-SERVICES.md read, table rewritten
- T+01:00 | mail-engine started by tick 1, reclassified from product to infrastructure dependency | services/mail-engine exists; back-in-stock and churn-buster both need it
- T+01:15 | tick 1 DIED mid-build at 03:53, work orphaned and unverified | mail-engine has models/database/auth/config/render/api-contract only; no routes, no main.py, no tests, no README
- T+01:15 | 04:00 scheduled tick DID NOT EXECUTE | supervisor last_run_at=null, next_run re-based to 04:15
- T+01:15 | duplicate 10m job aacda34412e5 PAUSED to stop contention for the agent slot | state=paused, last_run 03:37
- T+01:15 | resumed work moved to parallel subagents: finish mail-engine, scaffold back-in-stock | see below

## Resume state (read this before building)

mail-engine, partial. Existing: .python-version, pyproject.toml, docs/api-contract.md, mail_engine/{__init__,config,auth,database,models,render}.py
Missing: routes, main.py, seed script, tests, README. Files frozen at 03:53. Do not restart from scratch - finish it, then run 12 checks.
