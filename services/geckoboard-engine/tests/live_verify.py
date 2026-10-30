"""Independent live verification for Geckoboard Engine.

Run against an already-running server:
    GECKOBOARD_TEST_PORT=8641 GECKOBOARD_BASE=http://127.0.0.1:8641 \
    GECKOBOARD_ADMIN_PASSWORD=... GECKOBOARD_DATASET_API_KEY=... \
    python tests/live_verify.py
"""

from __future__ import annotations

import csv
import io
import os
import secrets
import sys
from urllib.parse import urlparse

import httpx

base_override = os.environ.get("GECKOBOARD_BASE")
TEST_PORT = int(
    os.environ.get(
        "GECKOBOARD_TEST_PORT",
        str(urlparse(base_override or "http://127.0.0.1:8670").port or 8670),
    )
)
BASE = base_override or f"http://127.0.0.1:{TEST_PORT}"
PASSWORD = os.environ["GECKOBOARD_ADMIN_PASSWORD"]
API_KEY = os.environ["GECKOBOARD_DATASET_API_KEY"]

passed: list[str] = []
failed: list[str] = []
statuses: list[int] = []
client = httpx.Client(timeout=10.0, base_url=BASE)


def call(method: str, path: str, **kwargs):
    response = client.request(method, path, **kwargs)
    statuses.append(response.status_code)
    return response


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        passed.append(name)
        print(f"PASS  {name}" + (f" :: {detail}" if detail else ""))
    else:
        failed.append(name)
        print(f"FAIL  {name}" + (f" :: {detail}" if detail else ""))


def main() -> int:
    health = call("get", "/health")
    check("health 200", health.status_code == 200, health.text[:120])
    check(
        "health reports configured port",
        health.json().get("port") == TEST_PORT,
        str(health.json().get("port")),
    )

    bad = call(
        "post",
        "/api/auth/login",
        json={"email": "admin@geckoboard.local", "password": secrets.token_urlsafe(16)},
    )
    check("bad password rejected", bad.status_code == 401, str(bad.status_code))

    unauth = call("get", "/api/admin/datasets")
    check("unauthenticated admin list rejected", unauth.status_code == 401, str(unauth.status_code))

    login = call(
        "post", "/api/auth/login", json={"email": "admin@geckoboard.local", "password": PASSWORD}
    )
    check(
        "good password returns session",
        login.status_code == 200 and bool(login.cookies),
        f"{login.status_code} cookies={len(login.cookies)}",
    )

    dataset_id = f"verify.{secrets.token_hex(3)}"
    fields = {
        "day": {"type": "date", "name": "Day", "optional": False},
        "team": {"type": "string", "name": "Team", "optional": False},
        "tickets": {"type": "number", "name": "Tickets", "optional": False},
    }
    created = call(
        "post",
        "/api/admin/datasets",
        json={"id": dataset_id, "fields": fields, "unique_by": ["day", "team"]},
    )
    check("dataset create writes row", created.status_code == 201, str(created.status_code))

    no_key = call(
        "post",
        f"/api/datasets/{dataset_id}/data",
        json={"data": [{"day": "2026-10-01", "team": "A", "tickets": 1}]},
    )
    check("push without key rejected", no_key.status_code == 401, str(no_key.status_code))

    rows = [
        {"day": "2026-10-01", "team": "A", "tickets": 3},
        {"day": "2026-10-01", "team": "B", "tickets": 5},
    ]
    pushed = call(
        "post", f"/api/datasets/{dataset_id}/data", json={"data": rows}, auth=(API_KEY, "")
    )
    check(
        "append accepts records",
        pushed.status_code == 200 and pushed.json()["inserted"] == 2,
        pushed.text[:120],
    )

    upsert = call(
        "post",
        f"/api/datasets/{dataset_id}/data",
        json={"data": [{"day": "2026-10-01", "team": "A", "tickets": 9}]},
        auth=(API_KEY, ""),
    )
    check(
        "unique_by upserts instead of duplicating",
        upsert.status_code == 200 and upsert.json()["total"] == 2,
        upsert.text[:120],
    )

    listed = call("get", f"/api/datasets/{dataset_id}/data", auth=(API_KEY, ""))
    values = sorted(r["tickets"] for r in listed.json()["data"])
    check(
        "read returns upserted values", listed.status_code == 200 and values == [5, 9], str(values)
    )

    replaced = call(
        "put",
        f"/api/datasets/{dataset_id}/data",
        json={"data": [{"day": "2026-10-02", "team": "C", "tickets": 7}]},
        auth=(API_KEY, ""),
    )
    check(
        "replace rewrites dataset",
        replaced.status_code == 200 and replaced.json()["total"] == 1,
        replaced.text[:120],
    )

    exported = call("get", f"/api/admin/datasets/{dataset_id}/export.csv")
    csv_rows = list(csv.DictReader(io.StringIO(exported.text)))
    check(
        "CSV export matches record count",
        exported.status_code == 200 and len(csv_rows) == 1 and csv_rows[0]["team"] == "C",
        f"{len(csv_rows)} rows",
    )

    dashboard = call(
        "post",
        "/api/admin/dashboards",
        json={
            "name": f"Verify {dataset_id}",
            "widgets": [
                {
                    "type": "number",
                    "title": "Tickets",
                    "dataset_id": dataset_id,
                    "metric_field": "tickets",
                    "aggregate": "sum",
                }
            ],
        },
    )
    dashboard_id = dashboard.json().get("id") if dashboard.status_code == 201 else None
    check(
        "dashboard create with number widget",
        dashboard.status_code == 201 and dashboard_id,
        str(dashboard.status_code),
    )

    preview = call("get", f"/api/admin/dashboards/{dashboard_id}/preview")
    widgets = preview.json().get("widgets", []) if preview.status_code == 200 else []
    check(
        "preview aggregates from stored records",
        preview.status_code == 200 and widgets and widgets[0].get("result", {}).get("value") == 7,
        str(widgets[:1])[:140],
    )

    rotation = call(
        "post",
        "/api/admin/rotations",
        json={"name": "Verify rotation", "interval_seconds": 15, "dashboard_ids": [dashboard_id]},
    )
    public_id = rotation.json().get("public_id") if rotation.status_code == 201 else None
    check(
        "rotation create returns public id",
        rotation.status_code == 201 and bool(public_id),
        str(rotation.status_code),
    )

    public = call("get", f"/api/public/rotations/{public_id}")
    public_ok = public.status_code == 200 and any(
        d.get("id") == dashboard_id for d in public.json().get("dashboards", [])
    )
    check("public rotation readable without auth", public_ok, str(public.status_code))

    tv = call("get", f"/tv/{public_id}")
    check(
        "TV page renders shell for rotation",
        tv.status_code == 200 and public_id in tv.text and "/static/tv.js" in tv.text,
        str(tv.status_code),
    )

    spec = call("get", "/openapi.json")
    paths = spec.json().get("paths", {})
    operations = sum(
        len([m for m in methods if m in {"get", "post", "put", "delete", "patch"}])
        for methods in paths.values()
    )
    check(
        "openapi route inventory",
        len(paths) == 16 and operations == 26,
        f"{len(paths)} paths / {operations} operations",
    )

    cleanup = call("delete", f"/api/admin/datasets/{dataset_id}")
    check("dataset delete removes row", cleanup.status_code == 200, str(cleanup.status_code))

    five_xx = [code for code in statuses if code >= 500]
    check("no 5xx across run", not five_xx, f"{len(statuses)} requests, 5xx={five_xx}")

    print(f"\nTOTAL {len(passed)} passed, {len(failed)} failed, {len(statuses)} requests")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
