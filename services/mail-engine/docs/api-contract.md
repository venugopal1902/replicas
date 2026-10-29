# Mail Engine API contract (v0.1.0)

Base URL: `http://localhost:8640`. JSON everywhere. Auth via `Authorization: Bearer <token>`
or the `mail_session` cookie set by login.

Seeded login: `admin@mailengine.local` / `mailengine123`.
Workspace: "Northvale Growth". Send mode is `simulate`: nothing leaves the machine.

All authenticated routes omit the token on failure -> `401 {"detail": "..."}`.
Every list response is a JSON object with an `items` array plus a count field.

## Health

`GET /health` (no auth)
```json
{"status":"online","service":"Mail Engine","version":"0.1.0","api":"/api","mcp":"/mcp","ui":"/","sendMode":"simulate"}
```

## Auth

### POST /api/auth/login  (no auth)
body `{"email":"admin@mailengine.local","password":"mailengine123"}`
-> 200 `{"token":"sess_...","user":{"id":"..","email":"..","name":".."},"workspace":{...}}`
bad password -> 401 `{"detail":"Invalid email or password"}`

### POST /api/auth/logout -> `{"ok":true}`
### GET /api/auth/me -> `{"user":{...},"workspace":{...},"via":"session"}`

Workspace shape:
```json
{"id":"..","name":"Northvale Growth","plan":"professional","dailyLimit":200,
 "sendingDomain":"northvale-outreach.com","warmupEnabled":true,"timezone":"Asia/Kolkata",
 "stats":{"mailboxes":3,"activeCampaigns":2,"leads":42,"sentToday":0,"replies":9}}
```

## Workspace

- `GET /api/workspace` -> `{"workspace":{...}}`
- `PATCH /api/workspace` body `{name?, dailyLimit?, sendingDomain?, warmupEnabled?}` -> `{"workspace":{...}}`

## Mailboxes

- `GET /api/mailboxes` -> `{"items":[...], "total":N}`
- `POST /api/mailboxes` body `{email, displayName?, provider?, dailyLimit?}` -> 201 `{"mailbox":{...}}`
- `PATCH /api/mailboxes/{id}` body `{status?, dailyLimit?, displayName?}` -> `{"mailbox":{...}}`
- `DELETE /api/mailboxes/{id}` -> `{"ok":true,"deleted":"<id>"}`

Mailbox shape:
```json
{"id":"..","email":"venu@northvale-outreach.com","displayName":"Venu G","provider":"simulated",
 "status":"active","dailyLimit":50,"sentToday":0,"warmupScore":91.4,"warmupEnabled":true,
 "remainingToday":50}
```

## Campaigns

- `GET /api/campaigns?status=` -> `{"items":[...],"total":N}`
- `POST /api/campaigns` body `{name, subject?, body?, dailyLimit?, fromMailboxId?}` -> 201 `{"campaign":{...}}`
- `GET /api/campaigns/{id}` -> `{"campaign":{...},"steps":[...],"stats":{...}}`
- `PATCH /api/campaigns/{id}` body `{name?,subject?,body?,status?,dailyLimit?,stopOnReply?,fromMailboxId?}` -> `{"campaign":{...}}`
- `DELETE /api/campaigns/{id}` -> `{"ok":true,"deleted":"<id>"}`
- `POST /api/campaigns/{id}/activate` -> `{"campaign":{...}}` (status -> active, sets activatedAt)
- `POST /api/campaigns/{id}/pause` -> `{"campaign":{...}}` (status -> paused)
- `POST /api/campaigns/{id}/preview` body `{leadId?}` -> `{"subject":"..","body":"..","html":"..","unknownTags":[],"subjectScore":{"score":100,"issues":[]}}`

Campaign shape:
```json
{"id":"..","name":"Q4 Data Engineering Outreach","status":"active","subject":"..","body":"..",
 "dailyLimit":60,"stopOnReply":true,"fromMailboxId":"..","trackingEnabled":true,
 "createdAt":"2026-10-30T03:00:00","activatedAt":"2026-10-18T03:00:00",
 "stats":{"enrolled":24,"sent":24,"opened":13,"clicked":4,"replied":4,"bounced":0,
          "openRate":54.2,"clickRate":16.7,"replyRate":16.7,"steps":3}}
```

## Sequence steps

- `GET /api/campaigns/{id}/steps` -> `{"items":[...],"total":N}`
- `POST /api/campaigns/{id}/steps` body `{stepOrder?, delayDays?, subject, body}` -> 201 `{"step":{...}}`
- `PATCH /api/steps/{id}` body `{stepOrder?,delayDays?,subject?,body?}` -> `{"step":{...}}`
- `DELETE /api/steps/{id}` -> `{"ok":true,"deleted":"<id>"}`

Step shape: `{"id":"..","campaignId":"..","stepOrder":1,"delayDays":0,"subject":"..","body":".."}`

## Leads

- `GET /api/leads?status=&q=&limit=` -> `{"items":[...],"total":N}`
- `POST /api/leads` body `{email, firstName?, lastName?, company?, title?}` -> 201 `{"lead":{...}}`
- `POST /api/leads/import` body `{"csv":"email,first_name,last_name,company,title\n..."}` -> `{"imported":N,"skipped":N,"errors":[".."]}`
- `PATCH /api/leads/{id}` body `{firstName?,lastName?,company?,title?,status?}` -> `{"lead":{...}}`
- `DELETE /api/leads/{id}` -> `{"ok":true,"deleted":"<id>"}`

Lead shape: `{"id":"..","email":"..","firstName":"..","lastName":"..","company":"..","title":"..","domain":"..","status":"new","source":"seed","createdAt":".."}`

## Enrollment and sending

- `POST /api/campaigns/{id}/enroll` body `{"leadIds":[".."]}` (or `{"all":true}`)
  -> `{"enrolled":N,"skipped":N,"reasons":{"suppressed":N,"already_enrolled":N}}`
- `GET /api/campaigns/{id}/enrollments` -> `{"items":[...],"total":N}`
- `POST /api/campaigns/{id}/send-next` body `{"limit":25}`
  -> `{"sent":N,"blocked":N,"finished":N,"sends":[{"id":"..","to":"..","step":1,"subject":"..","trackingId":".."}],"skipped":{"suppressed":N,"paused":N}}`
  Sending is simulated: rows are written, nothing is transmitted. A suppressed lead or an
  inactive mailbox blocks the send and is counted in `blocked`.
- `GET /api/sends?campaignId=&limit=` -> `{"items":[...],"total":N}`

## Tracking and unsubscribe (no auth)

- `GET /api/tracking/open/{tracking_id}.gif` -> 1x1 gif, stamps `openedAt` on first hit
- `GET /api/tracking/click/{tracking_id}` -> 302 to `PUBLIC_BASE_URL`, stamps `clickedAt`
- `POST /api/unsubscribe/{token}?email=..` -> `{"ok":true,"email":"..","suppressed":true}` (adds suppression)

## Inbox

- `GET /api/inbox?unread=true` -> `{"items":[...],"total":N,"unread":N}`
- `PATCH /api/inbox/{id}` body `{isRead:true}` -> `{"message":{...}}`
- `POST /api/inbox/{id}/reply` body `{"body":".."}` -> `{"ok":true,"send":{"id":".."},"message":{...}}`
  Reply writes an outbound Send row, marks the thread read, sets the lead status to `replied`.

Message shape: `{"id":"..","fromEmail":"..","fromName":"..","subject":"..","body":"..",
 "classification":"interested","isRead":false,"receivedAt":"..","campaignId":"..","leadId":".."}`

## Suppressions

- `GET /api/suppressions` -> `{"items":[...],"total":N}`
- `POST /api/suppressions` body `{email, reason?}` -> 201 `{"suppression":{...}}`
- `DELETE /api/suppressions/{id}` -> `{"ok":true,"deleted":"<id>"}`

## Warmup

- `GET /api/warmup` -> `{"items":[{"mailboxId":"..","email":"..","warmupScore":91.4,"days":[{"day":"2026-10-30","sent":40,"received":42,"landedInbox":40,"spam":0,"score":100.0}]}]}`
- `POST /api/warmup/run` -> `{"mailboxes":N,"daysCreated":N,"avgScore":93.2}` (advances today's warmup day per active mailbox)

## Analytics

- `GET /api/analytics/overview` ->
```json
{"totals":{"sent":40,"opened":21,"clicked":6,"replied":5,"bounced":0,"suppressed":4,
           "openRate":52.5,"clickRate":15.0,"replyRate":12.5},
 "campaigns":[{"id":"..","name":"..","status":"active","sent":24,"opened":13,"clicked":4,
               "replied":4,"openRate":54.2,"replyRate":16.7}],
 "daily":[{"day":"2026-10-24","sent":4,"opened":2,"replied":1}],
 "inbox":{"unread":4,"interested":4,"notNow":2,"unsubscribes":1}}
```

## MCP bridge

- `GET /mcp/tools` -> `{"tools":[{"name":"list_campaigns","description":"..","input":{}}]}`
- `POST /mcp/call` body `{"tool":"list_campaigns","arguments":{}}` -> `{"ok":true,"result":{...}}`
  Supported tools: `list_campaigns`, `get_campaign`, `create_campaign`, `list_leads`,
  `add_lead`, `enroll_leads`, `send_next`, `analytics_overview`.

## Verification surface

`GET /openapi.json` -> route count is asserted by the test suite (drift check).
