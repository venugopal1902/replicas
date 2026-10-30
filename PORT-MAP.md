# Port map (authoritative)

This file is the single source of truth for which service owns which port. When any agent or
contributor is unsure whether a port is contested, read this file and check `GET /health` on the
port. Do not re-raise a port question that this table already answers.

| Port | Service | Replaces | Status |
|---|---|---|---|
| 8400 | blotato-engine | Blotato | live, verified |
| 8500 | lead-engine | SalesQL | live, verified |
| 8600 | back-in-stock | Back in Stock by Amp | live, verified |
| 8610 | churn-buster | Churn Buster | live, verified |
| 8620 | bannerbear-engine | Bannerbear | live, verified |
| 8630 | zigpoll-engine | Zigpoll | live, verified |
| 8640 | mail-engine | Instantly / Lemlist | live, verified |
| 8650 | baremetrics-engine | Baremetrics | live, verified |
| 8660 | smile-engine | Smile.io | live, verified |
| 8670 | geckoboard-engine | Geckoboard | live, verified |

## Test ports

Suites that spawn their own server bind a throwaway port so they never disturb a live instance:

| Service | Env var | Port |
|---|---|---|
| back-in-stock | `BACKINSTOCK_TEST_PORT` | OS-assigned |
| churn-buster | `CHURN_TEST_PORT` | OS-assigned |
| bannerbear-engine | `BANNERBEAR_TEST_PORT` | OS-assigned |
| zigpoll-engine | `ZIGPOLL_TEST_PORT` | OS-assigned |
| baremetrics-engine | `BAREMETRICS_TEST_PORT` | OS-assigned |
| smile-engine | `SMILE_TEST_PORT` | OS-assigned |
| geckoboard-engine | `GECKOBOARD_TEST_PORT` | OS-assigned |

"OS-assigned" means the suite asks the operating system for a free port rather than assuming a
number. Windows can mark a band of ports unbindable (EACCES 10013) even with nothing listening, so
fixed test ports fail intermittently for reasons that have nothing to do with the service.

A suite must read its port from the env var and must never hard-code a literal port in an assertion.
A test that pins a stale port passes while the service fights another one for the socket.

## Settled decisions

- **Geckoboard is on 8670, permanently.** It was originally planned for 8640. When mail-engine
  claimed 8640, geckoboard moved to 8670 and its catalog page was published saying 8670. Displacing
  mail-engine to reclaim 8640 is explicitly **not** approved and is not needed. This decision does not
  require further sign-off.
- Services claim ports from 8600 upward, one each, recorded here before the first commit.
  blotato-engine and lead-engine keep their original 8400 and 8500.

## Before you raise a port conflict

1. Read this table.
2. `curl -s http://127.0.0.1:<port>/health` and see which service answers.
3. Check the catalog page for that service advertises the same port.

If all three agree, there is no conflict. `python site/verify_site.py` asserts step 3 automatically.
