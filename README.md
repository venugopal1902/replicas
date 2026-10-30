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
| [Blotato Engine](services/blotato-engine) | [Blotato](https://www.blotato.com/) | Python 3.12 / FastAPI / SQLite | [Read](https://presto-torque-aqk3.here.now/services/blotato-engine.html) |
| [Lead Engine](services/lead-engine) | [SalesQL](https://www.salesql.com/) | Python 3.12 / FastAPI / SQLite | [Read](https://presto-torque-aqk3.here.now/services/lead-engine.html) |

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

## Verifying a replica

Every service ships with proof, not claims. For the Blotato Engine:

```bash
cd services/blotato-engine

curl http://localhost:8400/health      # level 1: is it up
uv run python tests/test_engine.py     # level 2a: 52 API checks
uv run python tests/test_features.py   # level 2b: 13 live feature groups
```

### Test isolation

Suites must not depend on how many times they have run before. Four of them used to assert against
the long-running instance and wrote into its live database, so they accumulated state and eventually
failed for reasons unrelated to the code — mail-engine's e2e check passed when written, then went
permanently red once the shared mailbox burned its daily send quota.

Those suites now boot their own server on an OS-assigned port with a temporary database:

```python
BASE = start_isolated(
    service_dir=pathlib.Path(__file__).resolve().parents[1],
    db_env="SMILE_DB",
    test_port=int(os.environ.get("SMILE_TEST_PORT", "0")),   # 0 = let the OS choose
    live_port=8660,
    admin_password="smile123",
    admin_email="admin@smile.local",
)
```

The harness lives in `tools/isolated_server.py`. Set `<SERVICE>_LIVE_SUITE=1` to point a suite back
at the running instance for debugging. Two rules that came out of this:

- **Never hard-code a test port.** Windows can exclude a whole port band (Hyper-V, WSL, Docker) so a
  free-looking number fails to bind with EACCES 10013 while nothing is listening on it. Ask the OS.
- **Kill the process tree, not the wrapper.** `uv run` is a launcher; terminating it orphans the real
  server, which then holds the port and breaks the next run.

### Verify all ten services at once

```bash
python verify_all.py                 # start what is down, run every suite, stop what it started
python verify_all.py --keep          # leave the started services running afterwards
python verify_all.py --only smile-engine geckoboard-engine
python verify_all.py --skip-start    # only check what is already up
python verify_all.py --verbose       # print the tail of a failing suite
```

The harness knows each service's port, start command and suites. It never touches a service it did
not start: if the port already answers, it uses the running instance and leaves it alone. Services
that need a first-boot password get the local demo credentials, which are for a demo box only.

Expected output shape:

```
==============================================================
10/10 services verified, 310 checks passed
  PASS  blotato-engine          65/65
  PASS  lead-engine             50/50
  ...
```

Full per-service instructions, console walkthroughs and failure tables live in each
`services/<slug>/README.md`.

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
