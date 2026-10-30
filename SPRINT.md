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
| 5 | geckoboard-engine | Geckoboard | $79/319/mo | M | DONE | 32 | Dataset push (append/replace), widgets, dashboard preview, TV rotation, CSV, dataset API key. **Port 8670**, not 8640: mail-engine owns 8640. Chief re-verified 32/32 on :8641 after correcting the default back to 8670 |
| 6 | baremetrics-engine | Baremetrics | UNVERIFIED | M/L | DONE | 22 | MRR/ARR/ARPU/churn computed from rows, metrics recomputed independently in tests, as-of dates, MRR series, Stripe-shaped ingest. Port 8650 |
| 7 | smile-engine | Smile.io | $15/79/199/mo | L | DONE | 22 | Append-only points ledger, derived balances, idempotent earn/redeem, referrals with self-referral refused, tiers from lifetime earned. Port 8660 |

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

- T+02:20 | geckoboard-engine: chief tick found hermes's half-built tree, fixed 3 real bugs, executed 57 checks on :8670 | bugs: (1) partial unique index + `ON CONFLICT(dataset_id,unique_key)` without its `WHERE unique_key IS NOT NULL` guard -> every push to a `unique_by` dataset returned 500 (8 tests); (2) seeded widgets carry no `limit`, `_widget_result` indexed `widget["limit"]` -> seeded dashboard preview 500 and the console dashboard list died on "Request failed (500)"; (3) default port still 8640, moved to 8670 in config and tests. Evidence: 26/26 pytest, 20/20 live API (18 requests, 0 5xx, upsert total=2, replace total=1, preview value 7, 16 paths / 26 ops), 11/11 headless-Chrome UI (clear-cookie login, 3 tabs populated, no JS exceptions) = 57 passing
- T+02:22 | geckoboard-engine ownership collision caught: hermes re-took the tree 05:59-06:03 (main.py 34.3k -> 37.1k, new README, 31st test) while chief was verifying | chief stood down on all geckoboard files; row left VERIFY with hermes as sole writer. Last full suite after hermes's edit: 31 errors at fixture boot + 1 fail (413 vs 422 body limit), i.e. mid-edit, not a regression chief can judge
- T+02:22 | stale-uvicorn hazard: taskkill on the bash pid left the real uvicorn child holding 8670, so two "restarts" silently failed with WinError 10048 and I kept testing old code | resolve the actual PID first: `powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*<service>*' }"`, kill the uv child too, then confirm no LISTENING line
- T+04:20 | TEST-ROT HARDENING (post-sprint) | 6 of 10 suites now run against a private instance instead of the live service: back-in-stock, churn-buster, baremetrics, smile (2e5fe25) and lead-engine (8b8369a); bannerbear, zigpoll and geckoboard already isolated when built. Evidence: live databases byte-identical (sha256) after two consecutive full passes. Remaining unisolated: blotato-engine (32 mutating calls to live :8400) and mail-engine (24 to live :8640), both under read-only SSA review assigned to @hermes. Also fixed: lead-engine/scripts/run_server.py printed the admin password on every boot.
- T+04:05 | SERVICES WERE FOUND DOWN | all ten unresponsive; the background shells that launched them had exited, no crash. Restarted with `python verify_all.py --start-only`; 10/10 health 200, sweep 10/10 = 310 checks
- T+03:05 | SPRINT CLOSED: all 10 services verifiable from one command | `python verify_all.py` -> 10/10 services, 310 checks passed. Fixed 2 real defects found by the full run: my own count parser (read FAILED 0 as the total) and a mail-engine e2e check that enrolled the whole workspace and rotted once the shared mailbox hit its daily limit
- T+03:00 | geckoboard double-confirmed on the corrected default 8670 | chief 32/32 on :8641, hermes independently 32/32 on :8642, both zero 5xx. Commit 0eb4ebb present. 8640 stays Mail Engine
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

Port allocation: see [PORT-MAP.md](../PORT-MAP.md), which is authoritative. blotato 8400, lead 8500,
back-in-stock 8600, churn-buster 8610, bannerbear 8620, zigpoll 8630, mail-engine 8640,
baremetrics 8650, smile 8660, geckoboard 8670.

Next ranked row is 5 geckoboard-engine on :8670 (hermes), then 6 baremetrics-engine :8650 and
7 smile-engine :8660, both scoped by chief-bot before assignment.
Every tick: verify the row above you is DONE before starting a new service, and record literal
check counts - a subagent claim is not evidence.

Resume rule for tick after T+02:22: geckoboard-engine is VERIFY and hermes is the sole writer.
Chief must NOT edit files under services/geckoboard-engine while hermes is active (check mtimes;
anything touched in the last 5 minutes means wait). Chief's contribution there is recorded and
reproducible: tests/live_verify.py (20 checks) and tests/ui_smoke.mjs (11 checks). If hermes's
suite is green on the next tick, run those two against a fresh :8670 server and mark DONE;
if hermes has gone quiet with the 413 body-limit test still failing, chief fixes it then.
Do not start baremetrics-engine :8650 until geckoboard is DONE.
