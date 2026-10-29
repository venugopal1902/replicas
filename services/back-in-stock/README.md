# Back in Stock

Self-hosted restock waitlist and notification engine. A clean-room replacement for
[Back in Stock by Amp](https://backinstock.org/pricing), which charges $19 / $49 / $79 / $99 per month
for standard Shopify merchants.

Shoppers who hit a sold-out variant leave an email. When you restock that variant, every waiting
shopper gets a queued notification. Preorders are captured separately.

## What it does

- Product and variant catalogue with stock levels
- Public waitlist signup per variant, no account required
- Restock trigger: setting a variant from 0 to a positive stock queues one notification per waiter
- Preorder capture on sold-out variants
- Admin API: subscriptions, notifications, stats, per-variant CSV export
- Storefront demo at `/`, admin console at `/admin`

## What it deliberately does not do

- **No real email or SMS by default.** `SEND_MODE=dry_run` records every notification as a `dry_run`
  row. Nothing is transmitted. A notification row is never labelled `sent` unless SMTP was configured
  and actually used.
- No Shopify app installation. A real deployment additionally needs an OAuth app, webhook
  subscriptions for inventory levels, and an SMS provider for the SMS channel.
- No billing, no Shopify checkout integration, no multi-tenant store separation.

## Install and run

```bash
cd services/back-in-stock
uv sync
uv run python scripts/run_server.py
```

Server: `http://127.0.0.1:8600`
Storefront: `http://127.0.0.1:8600/`
Admin: `http://127.0.0.1:8600/admin`

Seeded admin: `admin@backinstock.local` / `backinstock123`

Change it before exposing the service:

```bash
uv run python - <<'PY'
from back_in_stock.app import connect, hash_password
with connect() as c:
    c.execute("UPDATE users SET password_hash=? WHERE email=?",
              (hash_password("your-new-password"), "admin@backinstock.local"))
    c.commit()
PY
```

## Verify

```bash
# with the server running on 8600
uv run python tests/test_back_in_stock.py
```

Recorded result on this machine: **20/20 checks passed**, including:

```
PASS  restock queues one notification per waiting subscriber  waiting_before=2 queued=2
PASS  notifications are dry_run, never claimed as delivered     statuses=['dry_run']
PASS  waiting subscriber flips to notified                      notified=1
PASS  CSV row count matches the subscription table              csv_rows=2 table_rows=2
PASS  zero HTTP 5xx across the run                              errors=[]
```

The suite picks a sold-out variant dynamically and forces one to zero if the catalogue is fully
stocked, so it is safe to re-run against an existing database.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | no | status, send mode, variant and waiting counts |
| POST | `/api/auth/login` | no | returns a bearer session token |
| POST | `/api/auth/logout` | bearer | revokes the session |
| GET | `/api/public/catalog` | no | storefront catalogue |
| POST | `/api/public/waitlist` | no | `{variant_id, email, phone?}` |
| POST | `/api/public/preorder` | no | `{variant_id, email, quantity}` |
| GET | `/api/variants` | bearer | variants with waiting counts |
| POST | `/api/variants/{id}/stock` | bearer | `{stock}`; queues notifications on 0 to positive |
| GET | `/api/subscriptions` | bearer | all waitlists, optional `?variant_id=` |
| GET | `/api/notifications` | bearer | notification log |
| GET | `/api/stats` | bearer | waiting, notified, preorders, sold-out counts |
| GET | `/api/variants/{id}/subscribers.csv` | bearer | CSV export for one variant |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8600` | listen port |
| `BACKINSTOCK_DB` | `data/backinstock.db` | sqlite path |
| `ADMIN_EMAIL` | `admin@backinstock.local` | seeded admin |
| `ADMIN_PASSWORD` | `backinstock123` | seeded password, first boot only |
| `SEND_MODE` | `dry_run` | `dry_run` records notifications without sending |

## Honest limits

- The notification queue is written and marked `dry_run` in the same request that restocks. A
  production version would hand off to a worker with retries and provider receipts.
- SMS is modelled as a channel and an address, but no SMS provider is integrated.
- Stock is set through the admin API. Real Shopify stock arrives through inventory-level webhooks.
