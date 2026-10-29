# Replicas

Self-hosted, clean-room replacements for the paid APIs and SaaS tools worth owning.

Live site: **https://venugopal1902.github.io/replicas/**

---

## What this is

A monorepo of reverse-engineered service replicas. Each one reproduces the behaviour and API
surface of a commercial product, written from scratch, tested end to end, and documented well
enough that someone can install it without asking me anything.

Two uses:

1. **Run it yourself.** No subscription, no post caps, no per-seat billing.
2. **Sell access to it.** Every service ships with a multi-tenant key system so you can meter
   usage and resell it under your own domain.

---

## Services

| Service | Replaces | Stack | Docs |
| --- | --- | --- | --- |
| [Blotato Engine](services/blotato-engine) | [Blotato](https://www.blotato.com/) | Python 3.12 / FastAPI / SQLite | [Read](https://venugopal1902.github.io/replicas/services/blotato-engine.html) |

---

## Repository layout

```
replicas/
├── replicas.json            # manifest: every service, all site content
├── services/
│   └── blotato-engine/      # full source for each replica
├── site/
│   └── build.py             # zero-dependency static site generator
└── docs/                    # generated site, served by GitHub Pages
```

---

## Adding a new replica

The site is generated from `replicas.json`, so adding a service is three steps:

1. Put the code in `services/<slug>/` with its own `README.md`.
2. Add an entry to `replicas.json` — overview, install steps, endpoints, notes, FAQ.
3. Rebuild and publish:

```bash
python site/build.py
git add -A
git commit -m "feat: add <slug> replica"
git push
```

GitHub Pages picks up the new `docs/` output automatically.

### Manifest fields

| Field | Purpose |
| --- | --- |
| `slug`, `name`, `tagline` | Identity and card copy |
| `replaces`, `replaces_url` | Which commercial product this clones |
| `status`, `version`, `license`, `language` | Badges and metadata |
| `summary`, `highlights`, `features` | Overview section |
| `prereqs`, `install`, `verify`, `urls` | Install guide |
| `usage` | Copy-paste examples |
| `endpoints`, `mcp_tools` | API reference tables |
| `monetization` | Pricing tiers for resale |
| `notes`, `faq` | Production gotchas and questions |

---

## Ground rules for every replica

- **Behaviour is cloned, code is not.** Reproduce the contract; write the implementation fresh.
- **Never copy proprietary source, assets or branding** from the original product.
- **A green end-to-end test is the shipping gate.** If the suite does not pass, the service is not done.
- **Document the failure modes.** Approval walls, rate limits and credential requirements go in
  `notes`, not in a support ticket later.
- **No secrets in the repo.** Keys come from environment variables or a secrets manager.

---

## Local preview

```bash
python -m http.server 8000 --directory docs
# open http://localhost:8000
```

---

## License

MIT — see [LICENSE](LICENSE). Replicas are independent implementations,
not affiliated with or endorsed by the products they are compatible with.
