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
| 4 | zigpoll-engine | Zigpoll | $29+ UNVERIFIED | M | DONE | 21 | Four question types, public id split from admin auth, HMAC webhooks, HTTPS-only destinations. Port 8630. Built by hermes, verified by chief |
| 5 | geckoboard-engine | Geckoboard | $79/319/mo | M | TODO | 0 | Custom datasets push API, widget composition, TV rotation mode. Port 8670 (8640 taken by mail-engine) |
| 6 | baremetrics-engine | Baremetrics | UNVERIFIED | M/L | TODO | 0 | MRR/ARR metrics from Stripe, churn, forecasting. Port 8650 |
| 7 | smile-engine | Smile.io | $15/79/199/mo | L | TODO | 0 | Points ledger, referrals, VIP tiers. Defer: ledger correctness + fraud controls. Port 8660 |

Infrastructure primitives, not replacements. Build only if a ranked row needs them:

| Service | Note | Status |
|---|---|---|
| mail-engine | SMTP send + sequence engine. Reusable by back-in-stock and churn-buster. Built as a full product surface anyway: campaigns, sequences, mailboxes, warmup, inbox, tracking, MCP. | DONE — 24 unit + 18 live + 15 UI = 57, port 8640 |
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

- T+02:00 | mail-engine FINISHED AND VERIFIED, infrastructure row BUILDING -> DONE, port 8640 (geckoboard moved to 8670) | routes/main/scripts/README/tests written, then executed: 24/24 unit, 18/18 live API (blocked-suppression path blocked=1, tracking pixel opened, e2e enroll->send->step advance, openapi 35 paths/47 ops, 0 5xx), 15/15 headless-Chrome UI (login, bad password, dashboard, campaign preview, leads search, inbox, mailboxes, settings, 0 JS exceptions). DB reseeded clean: 4 campaigns, 42 leads, 40 sends, health 200 on :8640
- T+01:55 | fixed 2 test bugs found by the first run, not backend bugs | unauth check used the logged-in cookie jar (200 -> 401 with a clean client); blocked-path check suppressed the lead before enrolling so the send-time block was never exercised (now blocked=1)

- T+02:10 | mail-engine independently RE-VERIFIED by chief-bot after the late cron tick | 24/24 unit + 18/18 live API executed, 42 requests zero 5xx, OpenAPI 35 paths/47 ops. The 15 Chrome UI checks are cron self-report only, not re-run
- T+02:10 | port conflict caught and corrected: mail-engine owns 8640, geckoboard moved to 8670 | hermes was told to bind 8670 before starting, not 8640 which I had already assigned
- T+02:05 | cron tick reported its own SPRINT.md edits as commit f4a5c3b, which was actually chief-bot's zigpoll commit that swept them in via git add -A | coordination lesson: never assume a commit hash in an agent report is that agent's own work
- T+01:36 | full portfolio re-verified in one pass, all six live | blotato 52+13, lead 50/50, back-in-stock 20/20, churn-buster 21/21, bannerbear 15, zigpoll 21 = 192 checks
- T+01:30 | bannerbear-engine VERIFIED AND PUBLISHED, row 3 TODO -> DONE | 15 pytest + independent E2E PNG 1080x1350 at 26089B, credit 5 -> 4; commits 11e4401, policy fix ab242f4, 68d09a1
- T+01:20 | admin password policy unified across catalog, no baked default and no printed secret | bannerbear reverted from generate-and-print to fail-fast with setup instructions; verified fresh DB refuses, existing DB still boots
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

Nothing is half-built. mail-engine was finished by a late cron tick and then independently re-verified
by chief-bot: 24/24 unit and 18/18 live API checks executed, 42 requests with zero 5xx, OpenAPI
35 paths / 47 operations. Its 15 headless-Chrome UI checks are cron self-report only and were NOT
re-run by chief-bot, so treat the UI layer as unverified until someone executes it.

Port allocation: blotato 8400, lead 8500, back-in-stock 8600, churn-buster 8610, bannerbear 8620,
zigpoll 8630, mail-engine 8640, geckoboard 8670.

Next ranked row is 5 geckoboard-engine on :8670 (hermes), then 6 baremetrics-engine :8650 and
7 smile-engine :8660, both scoped by chief-bot before assignment.
Every tick: verify the row above you is DONE before starting a new service, and record literal
check counts - a subagent claim is not evidence.
