# Baremetrics Engine

Self-hosted SaaS metrics engine. A clean-room replacement for [Baremetrics](https://baremetrics.com/),
which computes MRR, churn and recovery from your billing provider.

This engine computes the metrics **from subscription rows** rather than estimating them, and exposes
"as of date" numbers plus a daily MRR series.

## What it computes

| Metric | Definition used here |
|---|---|
| MRR | sum of `mrr_cents` for subscriptions started on/before the date and not cancelled on/before it |
| ARR | MRR × 12 |
| Active subscriptions | count under the same rule |
| ARPU | MRR ÷ active subscriptions |
| New MRR | sum of `mrr_cents` for subscriptions started in the last 30 days |
| Churned MRR | sum of `mrr_cents` for subscriptions cancelled in the last 30 days |
| Net new MRR | new − churned |
| Churn rate | churned MRR ÷ MRR at the start of the window |
| Quick ratio | new MRR ÷ churned MRR (null when nothing churned) |

A subscription cancelled **today** is excluded from today's MRR. That sounds obvious, but getting the
date comparison wrong is the classic way these dashboards overstate revenue, so it is asserted in tests.

## What it deliberately does not do

- No live billing-provider connection. Ingest is Stripe-*shaped* (`POST /webhooks/stripe`) so you can
  point a real webhook at it, and a simulator exists so everything works without credentials.
- No signature verification on the webhook. This endpoint ingests analytics data; it does not move money.
- No cohort tables, forecasting, segmentation or per-plan breakdowns.
- No price ladder is claimed for Baremetrics itself: its pricing was **UNVERIFIED** in our research pass.

## Install and run

`ADMIN_PASSWORD` is required on first boot. There is no baked default and nothing is printed to logs:

```bash
cd services/baremetrics-engine
uv sync
export ADMIN_PASSWORD='choose-a-long-unique-password'
uv run python scripts/run_server.py
```

- Console: `http://127.0.0.1:8650/`
- Health: `http://127.0.0.1:8650/health`
- Docs: `http://127.0.0.1:8650/docs`

If `ADMIN_PASSWORD` is absent and no admin row exists, startup fails with setup instructions.

## Verify

```bash
# server running on 8650
uv run python tests/test_baremetrics.py
```

Recorded on this machine: **22/22 checks passed**. The important ones recompute the headline metrics
independently from the raw subscription rows, so a wrong formula cannot pass:

```
PASS  MRR equals the sum of active subscriptions            api=334800 recomputed=334800
PASS  ARR is exactly 12x MRR                                arr=4017600
PASS  ARPU equals MRR divided by active count               api=6535 recomputed=6535
PASS  new MRR matches subscriptions started in the window   api=62900 recomputed=62900
PASS  churned MRR matches subscriptions canceled in the window api=11600 recomputed=11600
PASS  creating a subscription raises MRR by exactly its amount  before=334800 after=344700 delta=9900
PASS  cancelling it returns MRR to the previous value        afterCancel=334800
PASS  zero HTTP 5xx across the run                          errors=[]
```

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | no | status, subscription count, current MRR |
| POST | `/api/auth/login` | no | bearer token |
| GET | `/api/metrics/summary` | bearer | all headline metrics, optional `?as_of=YYYY-MM-DD` |
| GET | `/api/metrics/mrr-series` | bearer | daily MRR and active count, `?days=1..365` |
| GET | `/api/customers` | bearer | customers joined to their subscription |
| GET | `/api/subscriptions` | bearer | subscriptions, optional `?status=active\|canceled` |
| POST | `/api/subscriptions` | bearer | add a subscription |
| POST | `/api/subscriptions/{id}/cancel` | bearer | cancel, idempotent |
| GET | `/api/report.csv` | bearer | CSV of every subscription |
| POST | `/webhooks/stripe` | none | Stripe-shaped ingest |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8650` | listen port |
| `BAREMETRICS_DB` | `data/baremetrics.db` | sqlite path |
| `ADMIN_EMAIL` | `admin@baremetrics.local` | seeded admin |
| `ADMIN_PASSWORD` | none | **required on first boot** |

Seed data: 60 customers with a fixed random seed, 10 of them churned at varying distances in the past,
so the metrics are non-trivial and reproducible.

## Honest limits

- Metrics are computed on read from the subscription table. At large scale you would materialise daily
  snapshots instead of scanning on every request.
- Churn is revenue churn, not logo churn. There is no per-plan or per-cohort view.
- Webhook ingest trusts its payload. Put it behind your own signature check before exposing it publicly.
