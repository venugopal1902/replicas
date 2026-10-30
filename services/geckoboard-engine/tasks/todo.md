# Geckoboard Engine: Task List

- [x] Write executable acceptance tests first; all test URLs derive from `GECKOBOARD_TEST_PORT`, and health compares against that setting.
- [x] Implement config, SQLite schema, first-boot admin setup, HttpOnly admin sessions, and separate Basic-auth dataset API key.
- [x] Implement dataset definition/list/get/delete and append/replace push, typed validation, `unique_by`, pagination, CSV export, and row storage.
- [x] Implement dashboards and validated widget composition; compute preview values from dataset records.
- [x] Implement public TV rotation create/update/list and shareable `/tv/{public_id}` page/API.
- [x] Build local admin console, seed script with two sample dashboards, and setup/API documentation.
- [x] Run 32 acceptance checks on free port 8641, Ruff, Python compileall, JavaScript syntax checks, and dependency audit.
- [ ] Start on contract port 8640. Blocked: a live Mail Engine responds on 8640; do not stop or replace it without Venu's approval.
