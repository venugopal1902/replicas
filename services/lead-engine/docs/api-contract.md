# Lead Engine — API contract v1

Base URL: `http://localhost:8500`

Auth:
- Browser: `POST /api/auth/login` returns `{token}`; send `Authorization: Bearer <token>`
  (the UI also stores it in `localStorage.lead_token`).
- Machine: `x-api-key: lke_live_...`

All JSON unless noted. Errors: `{ "detail": "message" }` with 400/401/403/404/409/429.

Masking rule: unless a contact is revealed, `workEmail` and `directEmail` are returned as
`m***@domain.com` style masks and `revealed` is false.

---

## Auth

```
POST /api/auth/login      {email, password}      -> {token, user, workspace}
POST /api/auth/logout                            -> {ok:true}
GET  /api/auth/me                                -> {user, workspace}
POST /api/auth/signup     {email, password, name} -> {token, user, workspace}
```

`user` = `{id, email, name}`
`workspace` = `{id, name, plan, credits, verifierCredits, seats, seatsUsed, recipientsLimit, recipientsUsed, apiCallsLimit}`

## Workspace & credits

```
GET /api/workspace                     -> workspace
GET /api/credits/ledger?limit=50        -> {items:[{id, delta, pool, reason, contactId, balanceAfter, createdAt}]}
```

## Prospector search

```
POST /api/search
  { prompt?: string,
    filters?: { title?, company?, industry?, location?, headcount?, seniority?, hasDirectEmail?: bool },
    page?: 1, pageSize?: 20 }
-> { total, page, pageSize,
     items: [{ id, name, firstName, lastName, title, seniority, companyName, industry,
               headcount, location, linkedinUrl, revealed,
               workEmailMasked, directEmailMasked, hasPhone }] }

GET /api/search/suggestions -> { titles:[], companies:[], industries:[], locations:[], headcounts:[] }
```

Searching never costs credits.

## Reveal (costs 1 credit, only when an email is found)

```
POST /api/contacts/{id}/reveal
-> { contact, creditsSpent: 0|1, balance }

POST /api/contacts/reveal-bulk  { contactIds: [] }
-> { results: [{ contactId, ok, error? }], creditsSpent, balance }
```

`contact` when revealed:
```json
{ "id": "...", "name": "Maya Okafor", "firstName": "Maya", "lastName": "Okafor",
  "title": "Head of Talent", "companyName": "Northvale", "domain": "northvale.io",
  "industry": "SaaS", "headcount": "201-500", "location": "London, UK",
  "linkedinUrl": "https://www.linkedin.com/in/...",
  "revealed": true,
  "workEmail": "maya.okafor@northvale.io", "workEmailStatus": "verified",
  "workEmailScore": "A+", "workEmailPattern": "first.last",
  "directEmail": null, "directEmailStatus": "unavailable", "directEmailScore": null,
  "phone": null, "phoneSource": null,
  "enrichedAt": "2026-10-29T12:00:00" }
```

`workEmailStatus` ∈ `verified | risky | catch_all | invalid | unknown | unavailable`
`*Score` ∈ `A+ | A | B | C | null`

## Contacts

```
GET    /api/contacts?q=&folderId=&page=1&pageSize=20   -> { total, items:[contact] }
POST   /api/contacts        {firstName, lastName, companyName, domain?, title?, linkedinUrl?, location?}
DELETE /api/contacts/{id}
POST   /api/contacts/import {contacts:[{...}]}         -> { imported }
```

## Folders (lists)

```
GET    /api/folders                     -> {items:[{id,name,color,count,createdAt}]}
POST   /api/folders   {name, color}     -> folder
PATCH  /api/folders/{id} {name?, color?}
DELETE /api/folders/{id}
POST   /api/folders/{id}/contacts {contactIds:[]}       -> {added}
DELETE /api/folders/{id}/contacts/{contactId}
```

## Enrichment (LinkedIn → work email, direct email, phone)

```
POST /api/enrich/linkedin  { urls: ["https://www.linkedin.com/in/..."], folderId? } -> job
POST /api/enrich/identity  { firstName, lastName, companyName?, domain?, linkedinUrl?, title? }
     -> { contact, creditsSpent, balance }
POST /api/enrich/reverse-email { email } -> { contact|null, creditsSpent, balance }

GET /api/jobs            -> {items:[job]}
GET /api/jobs/{id}       -> job
GET /api/jobs/{id}/download -> text/csv attachment
```

`job` = `{ id, jobType, status, total, processed, found, creditsSpent, error, createdAt, finishedAt }`
`status` ∈ `queued | running | done | failed`

## CSV enrichment

```
POST /api/csv/enrich      multipart: file=<csv>, mode=url|lead|email, folderId?
     -> job   (mode=url expects a LinkedIn URL column,
               mode=lead expects first name + last name + company,
               mode=email expects an email column for reverse lookup)
GET  /api/csv/template?mode=url|lead|email  -> text/csv
```

## Email verifier (separate credit pool)

```
POST /api/verify { emails: ["a@b.com", ...] }
-> { items:[{email, result, score, mxHost, reason}], creditsSpent, verifierBalance }
```
`result` ∈ `valid | invalid | risky | catch_all | unknown`

## Campaigns

```
GET    /api/campaigns                         -> {items:[campaignSummary]}
POST   /api/campaigns    {name, fromEmail?, fromName?} -> campaign
GET    /api/campaigns/{id}                    -> {campaign, steps, stats}
PATCH  /api/campaigns/{id} {name?, status?, fromEmail?, fromName?, dailyLimit?}
DELETE /api/campaigns/{id}

POST   /api/campaigns/{id}/steps {subject, body, delayDays?}
DELETE /api/campaigns/{id}/steps/{stepId}

POST   /api/campaigns/{id}/recipients {contactIds:[]}  -> {added}
POST   /api/campaigns/{id}/send                        -> {queued, simulated}
GET    /api/campaigns/{id}/events?limit=50             -> {items:[{eventType, email, stepPosition, createdAt}]}
```

`stats` = `{recipients, sent, opened, clicked, replied, bounced}`
`campaignSummary` = `{id, name, status, fromEmail, stepCount, ...stats}`

Public tracking (no auth):
```
GET /t/o/{token}.gif     -> 1x1 gif, records an open
GET /t/c/{token}?u=<url> -> 302 redirect, records a click
```

## API keys

```
GET    /api/api-keys              -> {items:[{id,label,key,callsToday,callsLimit,createdAt}]}
POST   /api/api-keys {label}      -> {id,label,key,...}
DELETE /api/api-keys/{id}
```

## MCP

`POST /mcp` (JSON-RPC 2.0, same auth). Tools:
`search_prospects`, `reveal_contact`, `enrich_linkedin`, `verify_email`, `list_folders`, `check_credits`.

## Health

```
GET /health -> {status:"online", service:"Lead Engine", version, ui:"/"}
```
