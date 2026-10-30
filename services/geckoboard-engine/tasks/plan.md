# Geckoboard Engine: Implementation Plan

## Overview
Build a locally runnable Geckoboard-inspired service at `services/geckoboard-engine`, default port 8640. Support authenticated dataset definition and push, typed fields and records, widget composition with data previews, an admin console, and public TV rotations. The suite must use `GECKOBOARD_TEST_PORT` from its first run and never assume 8640 in health assertions.

## Architecture decisions
- FastAPI + SQLite, matching the existing replica stack; use FastAPI lifespan for schema/setup and HttpOnly admin session cookies.
- Keep admin login separate from the Datasets API key. Require explicit `GECKOBOARD_ADMIN_PASSWORD` at first boot; never print or generate a password. Require explicit dataset API key configuration for authenticated push operations.
- Follow official Datasets API concepts: PUT dataset definition, POST append records, PUT replace records, optional `unique_by`; model only the documented subset in README.
- Store dashboards as validated widget compositions; public TV rotations expose only dashboards explicitly included in a rotation.
- Default service port 8640, test port controlled by `GECKOBOARD_TEST_PORT`. Never stop or modify an existing listener.

## Phases and acceptance
1. **Foundation/auth + datasets**: first-boot password failure, cookie admin auth, Basic-auth dataset push key, dataset CRUD/definition and typed append/replace validation. Tests cover auth separation, key secrecy, unique keys, schema errors, and data persistence.
2. **Widgets + dashboards**: compose number, line, bar, leaderboard, and table widgets against one dataset each; verify previews from stored rows and admin protection.
3. **TV rotation + UI/seed**: create/edit/list rotations, public no-login TV route with timer-based rotation, admin UI, seed two datasets/dashboards with sample data.
4. **Verification**: at least 12 executable checks, Ruff, compileall, clean test DB and owned server, verify port state and report any live-port conflict without touching its process.

## Risks
- Port 8640 currently serves a live Mail Engine (`/health` returns HTTP 200). The Geckoboard service defaults to 8640, but do not bind there or stop Mail Engine without Venu's approval; the acceptance suite runs on free override port 8641.
- Exact commercial API quotas/features beyond the implemented documented subset are out of scope and must not be claimed as complete compatibility.
- No pricing claims in README unless verified from an official source.
