# Smile Engine

Self-hosted loyalty engine: points ledger, referrals and VIP tiers. Clean-room replacement for
[Smile.io](https://smile.io/pricing), listed at $15 / $79 / $199 per month.

## The design decision that matters

A loyalty program is a **ledger problem**, not a UI problem. So there is no mutable `balance` column
anywhere in this schema. Balance is always derived:

```
balance = SUM(points) over the member's ledger entries
```

That removes the whole class of bug where a balance drifts away from its history and nobody can
explain why. It also means every point that ever moved has a row, a reason and an idempotency key.

## What it does

- **Append-only ledger**: `earn`, `redeem` and `adjust` entries, each with a reason and a unique idempotency key
- **Idempotency everywhere**: replaying a request with the same key cannot double-award or double-spend
- **Refused redemptions write nothing**: if the balance is short, you get `409` and no entry appears
- **Referrals**: referrer and referee both earn, with self-referral and duplicate referees refused
- **VIP tiers** derived from **lifetime earned**, not spendable balance, so spending does not demote you
- **CSV export** of the whole ledger
- Console at `/`

## What it deliberately does not do

- No Shopify or BigCommerce integration. Members are created through the API.
- No email sending, no referral link hosting, no storefront widget.
- No points expiry, no fraud scoring beyond self-referral and duplicate-referee checks.
- No price ladder is claimed beyond Smile's published $15 / $79 / $199 tiers.

## Install and run

`ADMIN_PASSWORD` is required on first boot. No baked default, nothing printed to logs:

```bash
cd services/smile-engine
uv sync
export ADMIN_PASSWORD='choose-a-long-unique-password'
uv run python scripts/run_server.py
```

- Console: `http://127.0.0.1:8660/`
- Health: `http://127.0.0.1:8660/health`
- Docs: `http://127.0.0.1:8660/docs`

## Verify

```bash
# server running on 8660
uv run python tests/test_smile.py
```

Recorded on this machine: **22/22 checks passed on the first run**. The checks that actually protect
money:

```
PASS  earning 300 raises the balance by exactly 300                balance=300
PASS  replayed earn with the same idempotency key does not double-award  duplicate=True balance=300
PASS  earn without an idempotency key is refused with 422          status=422
PASS  redeeming a 150-point reward leaves 150                      balance=150 points=-150
PASS  redemption beyond the balance is refused with 409            status=409
PASS  a refused redemption writes no ledger entry                  balance=150
PASS  replayed redemption is idempotent, balance unchanged         duplicate=True balance=150
PASS  balance equals the sum of its ledger entries                 api=150 recomputed=150
PASS  tier is derived from lifetime earned, not spendable balance  lifetime=300 balance=150 tier=bronze
PASS  self-referral is refused with 409                            status=409
PASS  zero HTTP 5xx across the run                                 errors=[]
```

The test creates a timestamped member each run, so it is safe to re-run against an existing database.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | no | members, ledger entry count, points outstanding |
| POST | `/api/auth/login` | no | bearer token |
| GET | `/api/members` | bearer | all members with derived balance and tier |
| POST | `/api/members` | bearer | create a member, returns their referral code |
| GET | `/api/members/{id}` | bearer | one member with balance, lifetime earned, tier |
| POST | `/api/members/{id}/earn` | bearer | award points, requires `idempotency_key` |
| POST | `/api/members/{id}/redeem` | bearer | spend points on a reward, requires `idempotency_key` |
| GET | `/api/members/{id}/ledger` | bearer | that member's entries plus derived balance |
| POST | `/api/referrals` | bearer | award both sides of a referral |
| GET | `/api/rewards` | bearer | reward catalogue |
| GET | `/api/tiers` | bearer | tier thresholds |
| GET | `/api/report.csv` | bearer | full ledger export |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8660` | listen port |
| `SMILE_DB` | `data/smile.db` | sqlite path |
| `ADMIN_EMAIL` | `admin@smile.local` | seeded admin |
| `ADMIN_PASSWORD` | none | **required on first boot** |

Tiers: bronze 0, silver 500, gold 1500, platinum 5000 lifetime points.
Referrals: referrer +200, referee +100.

## Honest limits

- Ledger entries are never mutated or deleted, including on refunds. A reversal should be a new
  negative `adjust` entry, which the schema supports but no endpoint exposes yet.
- Concurrency is handled by SQLite's write lock. At real volume you would want a transactional
  balance check against a materialised view.
- No points expiry means outstanding liability grows forever. That is the next thing to build if this
  were ever used in production.
