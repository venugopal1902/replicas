# Geckoboard Engine: Task List

- [ ] Write executable acceptance tests first; all test URLs derive from `GECKOBOARD_TEST_PORT`, and health compares against that setting.
- [ ] Implement config, SQLite schema, first-boot admin setup, HttpOnly admin sessions, and separate Basic-auth dataset API key.
- [ ] Implement dataset definition/list/get/delete and append/replace push, typed validation, `unique_by`, and row storage.
- [ ] Implement dashboards and validated widget composition; compute preview values from dataset records.
- [ ] Implement public TV rotation create/update/list and shareable `/tv/{public_id}` page/API.
- [ ] Build local admin console, seed script with two sample dashboards, and setup/API documentation.
- [ ] Run suite on a free `GECKOBOARD_TEST_PORT`, Ruff, compileall, then verify the service can bind to 8640 without touching any existing process.
