# Churn Buster

Self-hosted failed-payment recovery engine. Clean-room replacement for
[Churn Buster](https://churnbuster.io/pricing/), whose dunning or cancel flows start at **$269/month**
and climb with MRR.

A customer's card fails. This engine opens a failure, enrols the customer in a dunning campaign,
queues the recovery emails, and cancels everything the moment the payment lands.

## What it does

- Failed-payment intake from a **Stripe-shaped webhook** (`invoice.payment_failed`, `charge.failed`)
  and from a local simulator
- Dunning campaigns with ordered steps and per-step delays (seeded default: immediate, +1 day, +3 days)
- Recovery: `invoice.paid` / `charge.succeeded` webhook or a manual mark, which cancels every pending email
- Duplicate suppression: a second failure for a customer who already has an open failure does not
  re-enrol them
- Recovery analytics: failure counts, recovery rate, MRR at risk, recovered MRR, queued vs dispatched
- Dispatch endpoint that promotes due emails to `dry_run` (or `sent` when SMTP is configured)
- Admin console at `/` and a CSV report

## What it deliberately does not do

- **No real email by default.** `SEND_MODE=dry_run`. An email row is never labelled `sent` unless SMTP
  was configured and actually used.
- **No live Stripe call.** The webhook *shape* is implemented, so a real endpoint URL works, but this
  does not call the Stripe API to retry a charge. Wire your own key to do that.
- **Signature verification is off until you set a secret.** With `STRIPE_WEBHOOK_SECRET` unset, payloads
  are accepted and labelled source `stripe-sim`. Set the secret and the same endpoint requires a valid
  `stripe-signature` (HMAC-SHA256 over `timestamp.body`).
- No cancellation flows / A-B tests / segmentation yet. Those are in the original's higher tier.

## Install and run

```bash
cd services/churn-buster
uv sync
uv run python scripts/run_server.py
```

- Admin console: `http://127.0.0.1:8610/`
- Health: `http://127.0.0.1:8610/health`
- Seeded admin: `admin@churnbuster.local` / `churnbuster123`

## Verify

```bash
# server running on 8610
uv run python tests/test_churn_buster.py
```

Recorded on this machine: **21/21 checks passed**, including:

```
PASS  failure queues one email per campaign step                queued=3 steps=3
PASS  second failure for the same open customer is not duplicated  duplicate=True
PASS  dispatch processes the due first-step email               processed=1
PASS  first step is dry_run, later steps stay queued            dry_run=1 still_queued=2
PASS  recovery succeeds and cancels pending emails              cancelled=2
PASS  stripe webhook payment recovers the failure               action=recovered
PASS  analytics counts match the failure table                   failures=2 recovered=2 rate=100.0%
PASS  zero HTTP 5xx across the run                              errors=[]
```

The suite creates a timestamped customer each run, so it is safe to re-run.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | no | status, send mode, open failures, queued emails |
| POST | `/api/auth/login` | no | bearer token |
| POST | `/api/auth/logout` | bearer | revoke session |
| GET | `/api/customers` | bearer | customers with open-failure counts |
| GET | `/api/failures` | bearer | failures, optional `?status=open\|recovered` |
| POST | `/api/simulate/payment-failure` | bearer | open a failure without Stripe |
| POST | `/api/failures/{id}/recover` | bearer | mark recovered, cancel pending emails |
| GET | `/api/campaigns` | bearer | campaigns with their steps |
| GET | `/api/emails` | bearer | dunning email queue |
| POST | `/api/dispatch` | bearer | promote due emails to dry_run/sent |
| GET | `/api/analytics` | bearer | recovery rate, MRR at risk, recovered MRR |
| GET | `/api/report.csv` | bearer | CSV of all failures |
| POST | `/webhooks/stripe` | signature | Stripe event intake |

## Stripe webhook example

```bash
curl -s -X POST http://127.0.0.1:8610/webhooks/stripe \
  -H 'Content-Type: application/json' \
  -d '{"type":"invoice.payment_failed","data":{"object":{"customer_email":"maya@example.com","amount_due":4900}}}'
```

```bash
curl -s -X POST http://127.0.0.1:8610/webhooks/stripe \
  -H 'Content-Type: application/json' \
  -d '{"type":"invoice.paid","data":{"object":{"customer_email":"maya@example.com","amount":4900}}}'
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8610` | listen port |
| `CHURN_DB` | `data/churnbuster.db` | sqlite path |
| `ADMIN_EMAIL` | `admin@churnbuster.local` | seeded admin |
| `ADMIN_PASSWORD` | `churnbuster123` | seeded password, first boot only |
| `SEND_MODE` | `dry_run` | `dry_run` records without sending |
| `STRIPE_WEBHOOK_SECRET` | empty | when set, webhooks require a valid signature |

## Honest limits

- Dispatch is pull-based through `POST /api/dispatch`. Production wants a scheduler, retries and
  provider delivery receipts.
- MRR at risk uses the stored `mrr_cents` per customer, not a live billing read.
- Charge retry logic belongs to Stripe. This engine orchestrates communication and reporting.
