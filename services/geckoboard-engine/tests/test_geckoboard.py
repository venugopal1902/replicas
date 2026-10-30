from __future__ import annotations

import csv
import io
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_PORT = int(os.environ.get("GECKOBOARD_TEST_PORT", "8670"))
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"
TEST_DIR = ROOT / "data-test"
DB_PATH = TEST_DIR / "acceptance.db"


@dataclass(repr=False)
class Service:
    client: httpx.Client
    admin_password: str
    dataset_key: str
    env: dict[str, str]
    process: subprocess.Popen[Any]
    seen_statuses: list[int] = field(default_factory=list)

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self.client.request(method, BASE_URL + path, **kwargs)
        self.seen_statuses.append(response.status_code)
        return response

    def admin_client(self) -> httpx.Client:
        return self.client

    def fresh_client(self) -> httpx.Client:
        return httpx.Client(timeout=5.0)

    def make_dataset(self, suffix: str | None = None) -> tuple[str, dict[str, Any]]:
        dataset_id = "acceptance." + (suffix or secrets.token_hex(4))
        fields = {
            "day": {"type": "date", "name": "Day", "optional": False},
            "team": {"type": "string", "name": "Team", "optional": False},
            "tickets": {"type": "number", "name": "Tickets", "optional": False},
            "revenue": {
                "type": "money",
                "name": "Revenue",
                "currency_code": "USD",
                "optional": True,
            },
        }
        response = self.request(
            "post",
            "/api/admin/datasets",
            json={"id": dataset_id, "fields": fields, "unique_by": ["day", "team"]},
        )
        assert response.status_code == 201, response.text
        return dataset_id, fields

    def push(self, method: str, dataset_id: str, records: list[dict[str, Any]]) -> httpx.Response:
        return self.request(
            method,
            f"/api/datasets/{dataset_id}/data",
            auth=(self.dataset_key, ""),
            json={"data": records},
        )


@pytest.fixture(scope="session")
def service() -> Service:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    sock = socket.socket()
    sock.settimeout(0.35)
    try:
        occupied = sock.connect_ex(("127.0.0.1", TEST_PORT)) == 0
    finally:
        sock.close()
    if occupied:
        pytest.fail(f"GECKOBOARD_TEST_PORT {TEST_PORT} is already listening; refusing to touch it")

    admin_password = secrets.token_urlsafe(32)
    dataset_key = secrets.token_urlsafe(32)
    env = os.environ.copy()
    env.update(
        {
            "GECKOBOARD_HOST": "127.0.0.1",
            "GECKOBOARD_PORT": str(TEST_PORT),
            "GECKOBOARD_TEST_PORT": str(TEST_PORT),
            "GECKOBOARD_DB_PATH": DB_PATH.as_posix(),
            "GECKOBOARD_ADMIN_PASSWORD": admin_password,
            "GECKOBOARD_DATASET_API_KEY": dataset_key,
            "GECKOBOARD_COOKIE_SECURE": "false",
            "GECKOBOARD_SEED_DEMO": "false",
            "PYTHONUNBUFFERED": "1",
        }
    )
    server_log = (TEST_DIR / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "geckoboard_engine.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(TEST_PORT),
            "--no-access-log",
        ],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=server_log,
    )
    client = httpx.Client(timeout=5.0)
    ready = False
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            response = client.get(BASE_URL + "/health")
            if response.status_code == 200:
                ready = True
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.15)
    if not ready:
        process.terminate()
        process.wait(timeout=5)
        client.close()
        server_log.close()
        pytest.fail(
            f"Geckoboard test server did not become ready on GECKOBOARD_TEST_PORT {TEST_PORT}"
        )

    svc = Service(client, admin_password, dataset_key, env, process)
    login = svc.request(
        "post",
        "/api/auth/login",
        json={"email": "admin@geckoboard.local", "password": admin_password},
    )
    assert login.status_code == 200, login.text
    yield svc

    client.close()
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    server_log.close()
    if os.environ.get("KEEP_GECKOBOARD_TEST_LOG") != "1":
        shutil.rmtree(TEST_DIR, ignore_errors=True)


def _basic_key(service: Service) -> tuple[str, str]:
    return service.dataset_key, ""


def _sample_records() -> list[dict[str, Any]]:
    return [
        {"day": "2026-05-01", "team": "North", "tickets": 12, "revenue": 1500.5},
        {"day": "2026-05-01", "team": "South", "tickets": 8, "revenue": 950.0},
        {"day": "2026-05-02", "team": "North", "tickets": 15, "revenue": 1900.0},
    ]


def test_01_health_uses_selected_test_port(service: Service) -> None:
    response = service.request("get", "/health")
    assert response.status_code == 200
    assert response.json()["port"] == TEST_PORT
    assert response.json()["service"] == "Geckoboard Engine"


def test_02_admin_routes_reject_missing_session(service: Service) -> None:
    with service.fresh_client() as client:
        response = client.get(BASE_URL + "/api/admin/datasets")
    assert response.status_code == 401


def test_03_admin_login_rejects_wrong_password(service: Service) -> None:
    with service.fresh_client() as client:
        response = client.post(
            BASE_URL + "/api/auth/login",
            json={"email": "admin@geckoboard.local", "password": secrets.token_urlsafe(24)},
        )
    assert response.status_code == 401


def test_04_login_sets_httponly_cookie_not_json_token(service: Service) -> None:
    with service.fresh_client() as client:
        response = client.post(
            BASE_URL + "/api/auth/login",
            json={"email": "admin@geckoboard.local", "password": service.admin_password},
        )
    assert response.status_code == 200
    assert "token" not in response.json()
    assert "httponly" in response.headers.get("set-cookie", "").lower()


def test_05_dataset_admin_crud_and_schema(service: Service) -> None:
    dataset_id, fields = service.make_dataset("crud")
    listed = service.request("get", "/api/admin/datasets")
    assert listed.status_code == 200
    item = next(d for d in listed.json()["items"] if d["id"] == dataset_id)
    assert item["fields"] == fields
    detail = service.request("get", f"/api/admin/datasets/{dataset_id}")
    assert detail.status_code == 200
    assert detail.json()["unique_by"] == ["day", "team"]


def test_06_dataset_push_key_is_separate_from_admin_session(service: Service) -> None:
    dataset_id, _ = service.make_dataset("separation")
    no_key = service.request(
        "post", f"/api/datasets/{dataset_id}/data", json={"data": _sample_records()}
    )
    assert no_key.status_code == 401
    admin_cookie_is_not_data_key = service.request(
        "get", f"/api/datasets/{dataset_id}", headers={"Authorization": "Bearer invalid"}
    )
    assert admin_cookie_is_not_data_key.status_code == 401
    accepted = service.push("post", dataset_id, _sample_records())
    assert accepted.status_code == 200


def test_07_official_dataset_api_key_auth_and_definition(service: Service) -> None:
    dataset_id = "acceptance.api." + secrets.token_hex(3)
    response = service.request(
        "put",
        f"/api/datasets/{dataset_id}",
        auth=_basic_key(service),
        json={
            "fields": {
                "timestamp": {"type": "datetime", "name": "Timestamp", "optional": False},
                "count": {"type": "number", "name": "Count", "optional": False},
            },
            "unique_by": ["timestamp"],
        },
    )
    assert response.status_code == 200
    assert response.json()["id"] == dataset_id
    unauthenticated = service.request("get", f"/api/datasets/{dataset_id}")
    assert unauthenticated.status_code == 401


def test_08_append_records_persist_and_list(service: Service) -> None:
    dataset_id, _ = service.make_dataset("append")
    response = service.push("post", dataset_id, _sample_records())
    assert response.status_code == 200
    assert response.json()["inserted"] == 3
    data = service.request("get", f"/api/datasets/{dataset_id}/data", auth=_basic_key(service))
    assert data.status_code == 200
    assert len(data.json()["data"]) == 3
    assert sum(row["tickets"] for row in data.json()["data"]) == 35


def test_09_unique_by_upserts_instead_of_duplicate_rows(service: Service) -> None:
    dataset_id, _ = service.make_dataset("unique")
    first = {"day": "2026-05-03", "team": "North", "tickets": 4}
    second = {"day": "2026-05-03", "team": "North", "tickets": 7}
    assert service.push("post", dataset_id, [first]).status_code == 200
    assert service.push("post", dataset_id, [second]).status_code == 200
    data = service.request("get", f"/api/datasets/{dataset_id}/data", auth=_basic_key(service))
    assert data.status_code == 200
    assert len(data.json()["data"]) == 1
    assert data.json()["data"][0]["tickets"] == 7


def test_10_invalid_typed_record_is_rejected(service: Service) -> None:
    dataset_id, _ = service.make_dataset("invalid-type")
    response = service.push(
        "post",
        dataset_id,
        [{"day": "2026-05-01", "team": "North", "tickets": "many"}],
    )
    assert response.status_code == 422


def test_11_replace_replaces_all_records(service: Service) -> None:
    dataset_id, _ = service.make_dataset("replace")
    assert service.push("post", dataset_id, _sample_records()).status_code == 200
    replacement = [{"day": "2026-05-09", "team": "West", "tickets": 2}]
    response = service.push("put", dataset_id, replacement)
    assert response.status_code == 200
    data = service.request("get", f"/api/datasets/{dataset_id}/data", auth=_basic_key(service))
    assert data.status_code == 200
    assert data.json()["data"] == replacement


def test_12_csv_export_row_count_matches_raw_records(service: Service) -> None:
    dataset_id, _ = service.make_dataset("csv")
    assert service.push("post", dataset_id, _sample_records()).status_code == 200
    response = service.request("get", f"/api/admin/datasets/{dataset_id}/export.csv")
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    data = service.request("get", f"/api/datasets/{dataset_id}/data", auth=_basic_key(service))
    assert len(rows) == len(data.json()["data"]) == 3


def test_13_dataset_api_admin_endpoints_require_cookie(service: Service) -> None:
    dataset_id, _ = service.make_dataset("admin-boundary")
    with service.fresh_client() as client:
        response = client.get(BASE_URL + f"/api/admin/datasets/{dataset_id}")
    assert response.status_code == 401


def test_14_dashboard_composes_distinct_widgets(service: Service) -> None:
    dataset_id, _ = service.make_dataset("widgets")
    assert service.push("post", dataset_id, _sample_records()).status_code == 200
    body = {
        "name": "Operations",
        "widgets": [
            {
                "id": "ticket-total",
                "type": "number",
                "title": "Tickets",
                "dataset_id": dataset_id,
                "metric_field": "tickets",
                "aggregate": "sum",
            },
            {
                "id": "tickets-by-team",
                "type": "bar",
                "title": "By team",
                "dataset_id": dataset_id,
                "metric_field": "tickets",
                "dimension_field": "team",
                "aggregate": "sum",
            },
            {
                "id": "ticket-table",
                "type": "table",
                "title": "Raw rows",
                "dataset_id": dataset_id,
                "limit": 10,
            },
        ],
    }
    response = service.request("post", "/api/admin/dashboards", json=body)
    assert response.status_code == 201, response.text
    widgets = response.json()["widgets"]
    assert [w["type"] for w in widgets] == ["number", "bar", "table"]


def test_15_dashboard_preview_aggregates_match_records(service: Service) -> None:
    dataset_id, _ = service.make_dataset("preview")
    assert service.push("post", dataset_id, _sample_records()).status_code == 200
    created = service.request(
        "post",
        "/api/admin/dashboards",
        json={
            "name": "KPI preview",
            "widgets": [
                {
                    "id": "sum",
                    "type": "number",
                    "title": "All tickets",
                    "dataset_id": dataset_id,
                    "metric_field": "tickets",
                    "aggregate": "sum",
                },
                {
                    "id": "group",
                    "type": "leaderboard",
                    "title": "Tickets by team",
                    "dataset_id": dataset_id,
                    "metric_field": "tickets",
                    "dimension_field": "team",
                    "aggregate": "sum",
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    dashboard_id = created.json()["id"]
    preview = service.request("get", f"/api/admin/dashboards/{dashboard_id}/preview")
    assert preview.status_code == 200
    widgets = {item["id"]: item["result"] for item in preview.json()["widgets"]}
    assert widgets["sum"]["value"] == 35
    assert widgets["group"]["items"][0]["value"] == 27


def test_16_time_series_requires_datetime_or_date_field(service: Service) -> None:
    dataset_id, _ = service.make_dataset("timeseries")
    assert service.push("post", dataset_id, _sample_records()).status_code == 200
    response = service.request(
        "post",
        "/api/admin/dashboards",
        json={
            "name": "Invalid line",
            "widgets": [
                {
                    "id": "bad-line",
                    "type": "line",
                    "title": "Bad",
                    "dataset_id": dataset_id,
                    "metric_field": "tickets",
                    "time_field": "team",
                }
            ],
        },
    )
    assert response.status_code == 422


def test_17_dashboard_update_and_list(service: Service) -> None:
    dataset_id, _ = service.make_dataset("dashboard-crud")
    created = service.request(
        "post", "/api/admin/dashboards", json={"name": "Initial", "widgets": []}
    )
    assert created.status_code == 201
    dashboard_id = created.json()["id"]
    updated = service.request(
        "put",
        f"/api/admin/dashboards/{dashboard_id}",
        json={
            "name": "Updated",
            "widgets": [
                {
                    "id": "count",
                    "type": "number",
                    "title": "Count",
                    "dataset_id": dataset_id,
                    "aggregate": "count",
                }
            ],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated"
    listed = service.request("get", "/api/admin/dashboards")
    assert any(item["id"] == dashboard_id for item in listed.json()["items"])


def test_18_public_rotation_has_tv_data_without_admin_auth(service: Service) -> None:
    dataset_id, _ = service.make_dataset("rotation")
    assert service.push("post", dataset_id, _sample_records()).status_code == 200
    dashboards = []
    for name in ("North wall", "South wall"):
        response = service.request(
            "post",
            "/api/admin/dashboards",
            json={
                "name": name,
                "widgets": [
                    {
                        "id": "tickets",
                        "type": "number",
                        "title": "Tickets",
                        "dataset_id": dataset_id,
                        "metric_field": "tickets",
                        "aggregate": "sum",
                    }
                ],
            },
        )
        assert response.status_code == 201
        dashboards.append(response.json()["id"])
    rotation = service.request(
        "post",
        "/api/admin/rotations",
        json={
            "name": "Office screens",
            "dashboard_ids": dashboards,
            "interval_seconds": 20,
            "active": True,
        },
    )
    assert rotation.status_code == 201
    public_id = rotation.json()["public_id"]
    with service.fresh_client() as client:
        public = client.get(BASE_URL + f"/api/public/rotations/{public_id}")
        tv = client.get(BASE_URL + f"/tv/{public_id}")
    assert public.status_code == 200
    assert len(public.json()["dashboards"]) == 2
    assert public.json()["interval_seconds"] == 20
    assert tv.status_code == 200
    assert "tv.js" in tv.text
    assert public_id in tv.text


def test_19_rotation_crud_and_inactive_rotation_is_hidden(service: Service) -> None:
    dashboard = service.request(
        "post", "/api/admin/dashboards", json={"name": "Empty display", "widgets": []}
    )
    assert dashboard.status_code == 201
    rotation = service.request(
        "post",
        "/api/admin/rotations",
        json={
            "name": "Disabled",
            "dashboard_ids": [dashboard.json()["id"]],
            "interval_seconds": 30,
            "active": True,
        },
    )
    assert rotation.status_code == 201
    rotation_id = rotation.json()["id"]
    public_id = rotation.json()["public_id"]
    updated = service.request("put", f"/api/admin/rotations/{rotation_id}", json={"active": False})
    assert updated.status_code == 200
    with service.fresh_client() as client:
        public = client.get(BASE_URL + f"/api/public/rotations/{public_id}")
    assert public.status_code == 404
    listed = service.request("get", "/api/admin/rotations")
    assert any(item["id"] == rotation_id for item in listed.json()["items"])


def test_20_admin_can_rotate_dataset_api_key_once(service: Service) -> None:
    old_key = service.dataset_key
    rotated = service.request("post", "/api/admin/credentials/dataset-key/rotate")
    assert rotated.status_code == 200
    new_key = rotated.json()["api_key"]
    assert new_key and new_key != old_key
    dataset_id, _ = service.make_dataset("key-rotate")
    old = service.request("get", f"/api/datasets/{dataset_id}", auth=(old_key, ""))
    new = service.request("get", f"/api/datasets/{dataset_id}", auth=(new_key, ""))
    assert old.status_code == 401
    assert new.status_code == 200
    service.dataset_key = new_key


def test_21_seed_script_creates_two_datasets_and_dashboards(service: Service) -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/seed_demo.py"],
        cwd=ROOT,
        env=service.env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.replace(
        service.admin_password, "[REDACTED]"
    ).replace(service.dataset_key, "[REDACTED]")
    assert "2 datasets" in completed.stdout
    datasets = service.request("get", "/api/admin/datasets")
    dashboards = service.request("get", "/api/admin/dashboards")
    assert len(datasets.json()["items"]) >= 2
    assert len(dashboards.json()["items"]) >= 2
    assert service.admin_password not in completed.stdout + completed.stderr
    assert service.dataset_key not in completed.stdout + completed.stderr


def test_22_first_boot_fails_without_admin_password_and_does_not_echo_secrets(
    service: Service,
) -> None:
    isolated = TEST_DIR / "missing-admin.db"
    env = service.env.copy()
    env["GECKOBOARD_DB_PATH"] = isolated.as_posix()
    env.pop("GECKOBOARD_ADMIN_PASSWORD", None)
    run = subprocess.run(
        [sys.executable, "-c", "from geckoboard_engine.database import init_db; init_db()"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0
    assert "GECKOBOARD_ADMIN_PASSWORD" in output
    assert service.dataset_key not in output
    isolated.unlink(missing_ok=True)


def test_23_first_boot_fails_without_dataset_api_key(service: Service) -> None:
    isolated = TEST_DIR / "missing-api-key.db"
    env = service.env.copy()
    env["GECKOBOARD_DB_PATH"] = isolated.as_posix()
    env.pop("GECKOBOARD_DATASET_API_KEY", None)
    run = subprocess.run(
        [sys.executable, "-c", "from geckoboard_engine.database import init_db; init_db()"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert run.returncode != 0
    assert "GECKOBOARD_DATASET_API_KEY" in run.stdout + run.stderr
    isolated.unlink(missing_ok=True)


def test_24_existing_database_starts_without_admin_password(service: Service) -> None:
    env = service.env.copy()
    env.pop("GECKOBOARD_ADMIN_PASSWORD", None)
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "from geckoboard_engine.database import init_db; init_db(); print('EXISTING_DB_OK')",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert "EXISTING_DB_OK" in run.stdout
    assert service.admin_password not in run.stdout + run.stderr


def test_25_admin_ui_is_local_only_and_has_no_external_assets(service: Service) -> None:
    response = service.request("get", "/")
    assert response.status_code == 200
    assert "Geckoboard Engine" in response.text
    assert "https://" not in response.text
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert "tv" in service.request("get", "/static/tv.js").text.lower()


def test_26_malformed_dataset_field_type_returns_validation_error(service: Service) -> None:
    response = service.request(
        "post",
        "/api/admin/datasets",
        json={
            "id": "acceptance.malformed-field",
            "fields": {"value": {"type": {"bad": "type"}, "name": "Value", "optional": False}},
        },
    )
    assert response.status_code == 422


def test_27_malformed_widget_type_returns_validation_error(service: Service) -> None:
    response = service.request(
        "post",
        "/api/admin/dashboards",
        json={
            "name": "Malformed widget",
            "widgets": [{"id": "bad", "type": {"bad": "type"}, "dataset_id": "support.tickets"}],
        },
    )
    assert response.status_code == 422


def test_28_rotation_active_requires_a_boolean(service: Service) -> None:
    dashboard = service.request(
        "post", "/api/admin/dashboards", json={"name": "Rotate", "widgets": []}
    )
    assert dashboard.status_code == 201
    response = service.request(
        "post",
        "/api/admin/rotations",
        json={
            "name": "Typed state",
            "dashboard_ids": [dashboard.json()["id"]],
            "interval_seconds": 30,
            "active": "false",
        },
    )
    assert response.status_code == 422


def test_29_dataset_record_list_supports_pagination(service: Service) -> None:
    dataset_id, _ = service.make_dataset("pagination")
    assert service.push("post", dataset_id, _sample_records()).status_code == 200
    response = service.request(
        "get", f"/api/datasets/{dataset_id}/data?limit=2&offset=1", auth=_basic_key(service)
    )
    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert len(response.json()["data"]) == 2
    assert response.json()["data"][0]["team"] == "South"


def test_large_request_body_is_rejected(service: Service) -> None:
    response = service.request(
        "post",
        "/api/admin/datasets",
        content=b" " * (2 * 1024 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_30_all_acceptance_requests_avoided_http_5xx(service: Service) -> None:
    assert not [code for code in service.seen_statuses if code >= 500]
