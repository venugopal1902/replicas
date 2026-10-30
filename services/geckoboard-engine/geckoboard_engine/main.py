from __future__ import annotations

import csv
import io
import json
import math
import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from .auth import hash_api_key, hash_session, verify_api_key, verify_password
from .config import BASE_DIR, settings
from .database import db_connection, init_db, now

STATIC_DIR = BASE_DIR / "static"
COOKIE_NAME = "geckoboard_session"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
BASIC = HTTPBasic(auto_error=False)
FIELD_TYPES = {"date", "datetime", "duration", "money", "number", "percentage", "string"}
NUMERIC_TYPES = {"duration", "money", "number", "percentage"}
WIDGET_TYPES = {"number", "line", "column", "bar", "leaderboard", "table"}
AGGREGATES = {"sum", "avg", "min", "max", "latest", "count"}
DATASET_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
FIELD_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,49}$")


def _unauthorized(message: str, scheme: str = "Bearer") -> HTTPException:
    return HTTPException(status_code=401, detail=message, headers={"WWW-Authenticate": scheme})


def current_admin(request: Request) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        raise _unauthorized("Admin session required")
    token_digest = hash_session(token)
    with db_connection() as conn:
        row = conn.execute("SELECT expires_at FROM sessions WHERE token_hash=?", (token_digest,)).fetchone()
        admin = conn.execute("SELECT id,email,display_name FROM admin_users WHERE id=1").fetchone()
    if not row or not admin:
        raise _unauthorized("Invalid admin session")
    if row["expires_at"] <= now():
        with db_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest,))
        raise _unauthorized("Admin session expired")
    return {"id": admin["id"], "email": admin["email"], "name": admin["display_name"]}


def require_dataset_key(credentials: HTTPBasicCredentials | None = Depends(BASIC)) -> None:
    if credentials is None or credentials.password:
        raise _unauthorized("Datasets API key required", "Basic")
    with db_connection() as conn:
        row = conn.execute("SELECT key_hash FROM dataset_api_credentials WHERE id=1").fetchone()
    if not row or not verify_api_key(credentials.username, row["key_hash"]):
        raise _unauthorized("Invalid Datasets API key", "Basic")


def _json(value: str) -> Any:
    return json.loads(value)


def _dataset_id(value: Any) -> str:
    if not isinstance(value, str) or not DATASET_ID_RE.fullmatch(value) or value in {".", ".."}:
        raise HTTPException(status_code=422, detail="Dataset id must use 1-100 letters, digits, dots, underscores, or hyphens")
    return value


def _validate_spec(dataset_id: str, body: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    fields = body.get("fields")
    if not isinstance(fields, dict) or not fields or len(fields) > 25:
        raise HTTPException(status_code=422, detail="fields must contain 1 to 25 field definitions")
    normalized: dict[str, Any] = {}
    for key, item in fields.items():
        if not isinstance(key, str) or not FIELD_ID_RE.fullmatch(key) or not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="Field names and definitions are invalid")
        field_type = item.get("type")
        name = item.get("name", key)
        optional = item.get("optional", False)
        if not isinstance(field_type, str) or field_type not in FIELD_TYPES or not isinstance(name, str) or not name.strip() or not isinstance(optional, bool):
            raise HTTPException(status_code=422, detail=f"Invalid definition for field {key}")
        clean = {"type": field_type, "name": name.strip(), "optional": optional}
        if field_type == "money":
            currency = item.get("currency_code")
            if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
                raise HTTPException(status_code=422, detail=f"Money field {key} requires a three-letter currency_code")
            clean["currency_code"] = currency
        normalized[key] = clean
    unique_by = body.get("unique_by", [])
    if not isinstance(unique_by, list) or any(not isinstance(key, str) or key not in normalized for key in unique_by):
        raise HTTPException(status_code=422, detail="unique_by must be an array of defined field names")
    if len(set(unique_by)) != len(unique_by):
        raise HTTPException(status_code=422, detail="unique_by cannot contain duplicate field names")
    return normalized, unique_by


def _get_dataset(conn, dataset_id: str):
    row = conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return row


def _dataset_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "fields": _json(row["fields_json"]),
        "unique_by": _json(row["unique_by_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _insert_dataset(dataset_id: str, fields: dict[str, Any], unique_by: list[str], allow_existing: bool) -> tuple[dict[str, Any], bool]:
    stamp = now()
    with db_connection() as conn:
        existing = conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if existing:
            if not allow_existing:
                raise HTTPException(status_code=409, detail="Dataset already exists")
            if _json(existing["fields_json"]) != fields or _json(existing["unique_by_json"]) != unique_by:
                raise HTTPException(status_code=409, detail="Dataset exists with a different schema")
            return _dataset_dict(existing), False
        conn.execute(
            "INSERT INTO datasets (id,fields_json,unique_by_json,created_at,updated_at) VALUES (?,?,?,?,?)",
            (dataset_id, json.dumps(fields, separators=(",", ":")), json.dumps(unique_by), stamp, stamp),
        )
        row = conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        return _dataset_dict(row), True


def _validate_record(record: Any, fields: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise HTTPException(status_code=422, detail=f"Record {index} must be an object")
    unknown = set(record) - set(fields)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Record {index} has unknown fields")
    normalized: dict[str, Any] = {}
    for key, definition in fields.items():
        if key not in record:
            if not definition["optional"]:
                raise HTTPException(status_code=422, detail=f"Record {index} is missing required field {key}")
            continue
        value = record[key]
        field_type = definition["type"]
        if value is None and definition["optional"]:
            normalized[key] = None
            continue
        if field_type == "string":
            valid = isinstance(value, str) and len(value) <= 5000
        elif field_type == "date":
            valid = isinstance(value, str)
            if valid:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    valid = False
        elif field_type == "datetime":
            valid = isinstance(value, str)
            if valid:
                try:
                    datetime.fromisoformat(value)
                except ValueError:
                    valid = False
        else:
            valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
        if not valid:
            raise HTTPException(status_code=422, detail=f"Record {index} has invalid {field_type} value for {key}")
        normalized[key] = value
    return normalized


def _store_records(dataset_id: str, body: dict[str, Any], replace: bool) -> dict[str, Any]:
    records = body.get("data")
    if not isinstance(records, list) or len(records) > 1000:
        raise HTTPException(status_code=422, detail="data must contain no more than 1,000 records")
    with db_connection() as conn:
        dataset = _get_dataset(conn, dataset_id)
        fields = _json(dataset["fields_json"])
        unique_by = _json(dataset["unique_by_json"])
        normalized = [_validate_record(record, fields, index) for index, record in enumerate(records)]
        existing_total = conn.execute(
            "SELECT COUNT(*) FROM dataset_records WHERE dataset_id=?", (dataset_id,)
        ).fetchone()[0]
        unique_keys = [
            json.dumps([record.get(key) for key in unique_by], separators=(",", ":"))
            for record in normalized
        ] if unique_by else []
        if replace:
            projected_total = len(set(unique_keys)) if unique_by else len(normalized)
        elif unique_by:
            existing_keys = {
                row[0]
                for row in conn.execute(
                    "SELECT unique_key FROM dataset_records WHERE dataset_id=? AND unique_key IS NOT NULL",
                    (dataset_id,),
                ).fetchall()
            }
            projected_total = existing_total + len(set(unique_keys) - existing_keys)
        else:
            projected_total = existing_total + len(normalized)
        if projected_total > 50000:
            raise HTTPException(status_code=422, detail="Dataset record limit is 50,000")
        if replace:
            conn.execute("DELETE FROM dataset_records WHERE dataset_id=?", (dataset_id,))
        for record in normalized:
            unique_key = json.dumps([record.get(key) for key in unique_by], separators=(",", ":")) if unique_by else None
            stamp = now()
            if unique_key is None:
                conn.execute(
                    "INSERT INTO dataset_records (dataset_id,record_json,unique_key,created_at) VALUES (?,?,NULL,?)",
                    (dataset_id, json.dumps(record, separators=(",", ":")), stamp),
                )
            else:
                conn.execute(
                    "INSERT INTO dataset_records (dataset_id,record_json,unique_key,created_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(dataset_id,unique_key) WHERE unique_key IS NOT NULL DO UPDATE SET record_json=excluded.record_json,created_at=excluded.created_at",
                    (dataset_id, json.dumps(record, separators=(",", ":")), unique_key, stamp),
                )
        conn.execute("UPDATE datasets SET updated_at=? WHERE id=?", (now(), dataset_id))
        total = conn.execute("SELECT COUNT(*) FROM dataset_records WHERE dataset_id=?", (dataset_id,)).fetchone()[0]
    return {"inserted": len(normalized), "total": total, "replaced": replace}


def _normalize_widget(widget: Any, index: int) -> dict[str, Any]:
    if not isinstance(widget, dict) or not isinstance(widget.get("type"), str) or widget.get("type") not in WIDGET_TYPES:
        raise HTTPException(status_code=422, detail=f"Widget {index} has an unsupported type")
    dataset_id = _dataset_id(widget.get("dataset_id"))
    with db_connection() as conn:
        dataset = _get_dataset(conn, dataset_id)
        fields = _json(dataset["fields_json"])
    widget_type = widget["type"]
    aggregate = widget.get("aggregate", "sum")
    if not isinstance(aggregate, str) or aggregate not in AGGREGATES:
        raise HTTPException(status_code=422, detail=f"Widget {index} has an invalid aggregate")
    metric = widget.get("metric_field")
    if widget_type != "table" and aggregate != "count" and (
        not isinstance(metric, str) or metric not in fields or fields[metric]["type"] not in NUMERIC_TYPES
    ):
        raise HTTPException(status_code=422, detail=f"Widget {index} requires a numeric metric_field")
    if widget_type in {"bar", "column", "leaderboard"}:
        dimension = widget.get("dimension_field")
        if not isinstance(dimension, str) or dimension not in fields or fields[dimension]["type"] not in {"date", "datetime", "string"}:
            raise HTTPException(status_code=422, detail=f"Widget {index} requires a categorical dimension_field")
    if widget_type == "line":
        time_field = widget.get("time_field")
        if not isinstance(time_field, str) or time_field not in fields or fields[time_field]["type"] not in {"date", "datetime"}:
            raise HTTPException(status_code=422, detail=f"Widget {index} requires a date or datetime time_field")
    limit = widget.get("limit", 20)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail=f"Widget {index} limit must be between 1 and 100")
    clean = {
        "id": str(widget.get("id") or f"widget-{secrets.token_hex(4)}")[:80],
        "type": widget_type,
        "title": str(widget.get("title") or widget_type.title())[:120],
        "dataset_id": dataset_id,
        "aggregate": aggregate,
        "limit": limit,
    }
    if metric is not None:
        clean["metric_field"] = metric
    for key in ("dimension_field", "time_field"):
        if key in widget:
            clean[key] = widget[key]
    for key, default, minimum, maximum in (("x", 0, 0, 11), ("y", index * 2, 0, 100), ("width", 6, 1, 12), ("height", 2, 1, 8)):
        value = widget.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise HTTPException(status_code=422, detail=f"Widget {index} has invalid layout field {key}")
        clean[key] = value
    return clean


def _normalize_widgets(widgets: Any) -> list[dict[str, Any]]:
    if not isinstance(widgets, list) or len(widgets) > 12:
        raise HTTPException(status_code=422, detail="widgets must be an array with at most 12 items")
    normalized = [_normalize_widget(widget, index) for index, widget in enumerate(widgets)]
    ids = [widget["id"] for widget in normalized]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Widget ids must be unique within a dashboard")
    return normalized


def _number(values: list[Any], aggregate: str, row_count: int) -> float | int:
    if aggregate == "count":
        return row_count
    numbers = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if not numbers:
        return 0
    result = {
        "sum": lambda: sum(numbers),
        "avg": lambda: sum(numbers) / len(numbers),
        "min": lambda: min(numbers),
        "max": lambda: max(numbers),
        "latest": lambda: numbers[-1],
    }[aggregate]()
    return round(result, 4)


def _widget_result(widget: dict[str, Any]) -> dict[str, Any]:
    limit = int(widget.get("limit") or 20)
    aggregate = widget.get("aggregate") or "sum"
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT record_json FROM dataset_records WHERE dataset_id=? ORDER BY id ASC",
            (widget["dataset_id"],),
        ).fetchall()
    records = [_json(row["record_json"]) for row in rows]
    kind = widget["type"]
    if kind == "table":
        return {"items": list(reversed(records[-limit:]))}
    metric = widget.get("metric_field")
    if kind == "number":
        return {"value": _number([record.get(metric) for record in records] if metric else [], aggregate, len(records))}
    group_key = widget.get("time_field") if kind == "line" else widget.get("dimension_field")
    groups: dict[str, list[Any]] = {}
    for record in records:
        label = str(record.get(group_key, "Unknown"))
        groups.setdefault(label, []).append(record.get(metric) if metric else 1)
    items = [{"label": label, "value": _number(values, aggregate, len(values))} for label, values in groups.items()]
    if kind == "line":
        items.sort(key=lambda item: item["label"])
    else:
        items.sort(key=lambda item: (-float(item["value"]), item["label"]))
    return {"items": items[:limit]}


def _dashboard_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "widgets": _json(row["widgets_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _dashboard_preview(row) -> dict[str, Any]:
    dashboard = _dashboard_dict(row)
    dashboard["widgets"] = [
        {**widget, "result": _widget_result(widget)} for widget in dashboard["widgets"]
    ]
    return dashboard


def _rotation_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "public_id": row["public_id"],
        "name": row["name"],
        "dashboard_ids": _json(row["dashboard_ids_json"]),
        "interval_seconds": row["interval_seconds"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    raw_length = request.headers.get("content-length")
    if raw_length is None:
        response = await call_next(request)
    else:
        try:
            body_length = int(raw_length)
        except ValueError:
            response = PlainTextResponse("Invalid Content-Length", status_code=400)
        else:
            response = (
                PlainTextResponse("Request body too large", status_code=413)
                if body_length > MAX_REQUEST_BYTES
                else await call_next(request)
            )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "object-src 'none'; base-uri 'none'; "
        + ("frame-ancestors *" if request.url.path.startswith("/tv/") else "frame-ancestors 'self'")
    )
    if not request.url.path.startswith("/tv/"):
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    with db_connection() as conn:
        datasets = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        dashboards = conn.execute("SELECT COUNT(*) FROM dashboards").fetchone()[0]
    return {"status": "online", "service": settings.app_name, "version": settings.version, "port": settings.port, "datasets": datasets, "dashboards": dashboards}


@app.post("/api/auth/login")
def login(body: dict[str, Any] = Body(...), response: Response = None) -> dict[str, Any]:
    email = str(body.get("email", "")).strip().lower()
    password = body.get("password")
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM admin_users WHERE lower(email)=?", (email,)).fetchone()
        if not row or not isinstance(password, str) or not verify_password(password, row["password_hash"]):
            raise _unauthorized("Invalid email or password")
        token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(hours=settings.session_hours)
        conn.execute("INSERT INTO sessions (token_hash,expires_at,created_at) VALUES (?,?,?)", (hash_session(token), expires.isoformat(timespec="seconds"), now()))
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=settings.session_hours * 3600, path="/")
    return {"ok": True, "user": {"email": row["email"], "name": row["display_name"]}}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, admin: dict[str, Any] = Depends(current_admin)) -> dict[str, bool]:
    token = request.cookies.get(COOKIE_NAME, "")
    with db_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (hash_session(token),))
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
    return {"ok": True}


@app.get("/api/")
def ping(_: None = Depends(require_dataset_key)) -> dict[str, Any]:
    return {}


@app.post("/api/admin/credentials/dataset-key/rotate")
def rotate_dataset_key(_: dict[str, Any] = Depends(current_admin)) -> dict[str, str]:
    key = secrets.token_urlsafe(32)
    with db_connection() as conn:
        conn.execute("UPDATE dataset_api_credentials SET key_hash=?,created_at=? WHERE id=1", (hash_api_key(key), now()))
    return {"api_key": key, "notice": "Shown once. Copy it now; it is not stored in plaintext."}


@app.get("/api/admin/datasets")
def admin_list_datasets(_: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
    with db_connection() as conn:
        rows = conn.execute("SELECT * FROM datasets ORDER BY id").fetchall()
    return {"items": [_dataset_dict(row) for row in rows], "total": len(rows)}


@app.post("/api/admin/datasets", status_code=status.HTTP_201_CREATED)
def admin_create_dataset(body: dict[str, Any] = Body(...), _: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
    dataset_id = _dataset_id(body.get("id"))
    fields, unique_by = _validate_spec(dataset_id, body)
    dataset, _ = _insert_dataset(dataset_id, fields, unique_by, allow_existing=False)
    return dataset


@app.get("/api/admin/datasets/{dataset_id}")
def admin_get_dataset(dataset_id: str, _: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
    with db_connection() as conn:
        return _dataset_dict(_get_dataset(conn, _dataset_id(dataset_id)))


@app.delete("/api/admin/datasets/{dataset_id}")
def admin_delete_dataset(dataset_id: str, _: dict[str, Any] = Depends(current_admin)) -> dict[str, bool]:
    with db_connection() as conn:
        cursor = conn.execute("DELETE FROM datasets WHERE id=?", (_dataset_id(dataset_id),))
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Dataset not found")
    return {"ok": True}


@app.put("/api/datasets/{dataset_id}")
def api_define_dataset(dataset_id: str, body: dict[str, Any] = Body(...), _: None = Depends(require_dataset_key)) -> dict[str, Any]:
    dataset_id = _dataset_id(dataset_id)
    fields, unique_by = _validate_spec(dataset_id, body)
    dataset, _ = _insert_dataset(dataset_id, fields, unique_by, allow_existing=True)
    return dataset


@app.get("/api/datasets/{dataset_id}")
def api_get_dataset(dataset_id: str, _: None = Depends(require_dataset_key)) -> dict[str, Any]:
    with db_connection() as conn:
        return _dataset_dict(_get_dataset(conn, _dataset_id(dataset_id)))


@app.delete("/api/datasets/{dataset_id}")
def api_delete_dataset(dataset_id: str, _: None = Depends(require_dataset_key)) -> dict[str, bool]:
    with db_connection() as conn:
        cursor = conn.execute("DELETE FROM datasets WHERE id=?", (_dataset_id(dataset_id),))
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Dataset not found")
    return {"ok": True}


@app.post("/api/datasets/{dataset_id}/data")
def api_append_data(dataset_id: str, body: dict[str, Any] = Body(...), _: None = Depends(require_dataset_key)) -> dict[str, Any]:
    return _store_records(_dataset_id(dataset_id), body, replace=False)


@app.put("/api/datasets/{dataset_id}/data")
def api_replace_data(dataset_id: str, body: dict[str, Any] = Body(...), _: None = Depends(require_dataset_key)) -> dict[str, Any]:
    return _store_records(_dataset_id(dataset_id), body, replace=True)


@app.get("/api/datasets/{dataset_id}/data")
def api_list_data(
    dataset_id: str,
    limit: int = Query(default=1000, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_dataset_key),
) -> dict[str, Any]:
    dataset_id = _dataset_id(dataset_id)
    with db_connection() as conn:
        _get_dataset(conn, dataset_id)
        total = conn.execute(
            "SELECT COUNT(*) FROM dataset_records WHERE dataset_id=?", (dataset_id,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT record_json FROM dataset_records WHERE dataset_id=? ORDER BY id LIMIT ? OFFSET ?",
            (dataset_id, limit, offset),
        ).fetchall()
    return {
        "data": [_json(row["record_json"]) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/admin/datasets/{dataset_id}/export.csv")
def export_csv(dataset_id: str, _: dict[str, Any] = Depends(current_admin)) -> PlainTextResponse:
    with db_connection() as conn:
        dataset = _get_dataset(conn, _dataset_id(dataset_id))
        fields = _json(dataset["fields_json"])
        rows = conn.execute("SELECT record_json FROM dataset_records WHERE dataset_id=? ORDER BY id", (dataset_id,)).fetchall()
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(fields), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(_json(row["record_json"]))
    return PlainTextResponse(buffer.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{dataset_id.replace(".", "-")}.csv"'})


@app.get("/api/admin/dashboards")
def admin_list_dashboards(_: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
    with db_connection() as conn:
        rows = conn.execute("SELECT * FROM dashboards ORDER BY name").fetchall()
    return {"items": [_dashboard_dict(row) for row in rows], "total": len(rows)}


@app.post("/api/admin/dashboards", status_code=status.HTTP_201_CREATED)
def admin_create_dashboard(body: dict[str, Any] = Body(...), _: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
    name = body.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise HTTPException(status_code=422, detail="Dashboard name must be 1-100 characters")
    widgets = _normalize_widgets(body.get("widgets", []))
    dashboard_id = "dash-" + secrets.token_hex(8)
    stamp = now()
    with db_connection() as conn:
        conn.execute("INSERT INTO dashboards (id,name,widgets_json,created_at,updated_at) VALUES (?,?,?,?,?)", (dashboard_id, name.strip(), json.dumps(widgets, separators=(",", ":")), stamp, stamp))
        row = conn.execute("SELECT * FROM dashboards WHERE id=?", (dashboard_id,)).fetchone()
    return _dashboard_dict(row)


@app.get("/api/admin/dashboards/{dashboard_id}")
def admin_get_dashboard(dashboard_id: str, _: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM dashboards WHERE id=?", (dashboard_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return _dashboard_dict(row)


@app.put("/api/admin/dashboards/{dashboard_id}")
def admin_update_dashboard(dashboard_id: str, body: dict[str, Any] = Body(...), _: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM dashboards WHERE id=?", (dashboard_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        name = body.get("name", row["name"])
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise HTTPException(status_code=422, detail="Dashboard name must be 1-100 characters")
        widgets = _normalize_widgets(body.get("widgets", _json(row["widgets_json"])))
        conn.execute("UPDATE dashboards SET name=?,widgets_json=?,updated_at=? WHERE id=?", (name.strip(), json.dumps(widgets, separators=(",", ":")), now(), dashboard_id))
        updated = conn.execute("SELECT * FROM dashboards WHERE id=?", (dashboard_id,)).fetchone()
    return _dashboard_dict(updated)


@app.delete("/api/admin/dashboards/{dashboard_id}")
def admin_delete_dashboard(dashboard_id: str, _: dict[str, Any] = Depends(current_admin)) -> dict[str, bool]:
    with db_connection() as conn:
        cursor = conn.execute("DELETE FROM dashboards WHERE id=?", (dashboard_id,))
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        rotations = conn.execute("SELECT id,dashboard_ids_json FROM rotations").fetchall()
        for rotation in rotations:
            ids = [item for item in _json(rotation["dashboard_ids_json"]) if item != dashboard_id]
            conn.execute("UPDATE rotations SET dashboard_ids_json=?,updated_at=? WHERE id=?", (json.dumps(ids), now(), rotation["id"]))
    return {"ok": True}


@app.get("/api/admin/dashboards/{dashboard_id}/preview")
def admin_dashboard_preview(dashboard_id: str, _: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM dashboards WHERE id=?", (dashboard_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    dashboard = _dashboard_preview(row)
    return {"id": dashboard["id"], "name": dashboard["name"], "widgets": dashboard["widgets"]}


@app.get("/api/admin/rotations")
def admin_list_rotations(_: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
    with db_connection() as conn:
        rows = conn.execute("SELECT * FROM rotations ORDER BY name").fetchall()
    return {"items": [_rotation_dict(row) for row in rows], "total": len(rows)}


def _check_dashboard_ids(conn, dashboard_ids: Any) -> list[str]:
    if not isinstance(dashboard_ids, list) or not 1 <= len(dashboard_ids) <= 10 or any(not isinstance(item, str) for item in dashboard_ids):
        raise HTTPException(status_code=422, detail="dashboard_ids must contain 1 to 10 dashboard ids")
    if len(set(dashboard_ids)) != len(dashboard_ids):
        raise HTTPException(status_code=422, detail="dashboard_ids cannot contain duplicates")
    for dashboard_id in dashboard_ids:
        if not conn.execute("SELECT id FROM dashboards WHERE id=?", (dashboard_id,)).fetchone():
            raise HTTPException(status_code=422, detail="Rotation references an unknown dashboard")
    return dashboard_ids


@app.post("/api/admin/rotations", status_code=status.HTTP_201_CREATED)
def admin_create_rotation(body: dict[str, Any] = Body(...), _: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
    name = body.get("name")
    interval = body.get("interval_seconds", 30)
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise HTTPException(status_code=422, detail="Rotation name must be 1-100 characters")
    if not isinstance(interval, int) or isinstance(interval, bool) or not 10 <= interval <= 600:
        raise HTTPException(status_code=422, detail="interval_seconds must be between 10 and 600")
    active = body.get("active", True)
    if not isinstance(active, bool):
        raise HTTPException(status_code=422, detail="active must be a boolean")
    with db_connection() as conn:
        dashboard_ids = _check_dashboard_ids(conn, body.get("dashboard_ids"))
        rotation_id = "rotation-" + secrets.token_hex(8)
        public_id = secrets.token_urlsafe(16)
        stamp = now()
        conn.execute("INSERT INTO rotations (id,public_id,name,dashboard_ids_json,interval_seconds,active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (rotation_id, public_id, name.strip(), json.dumps(dashboard_ids), interval, int(active), stamp, stamp))
        row = conn.execute("SELECT * FROM rotations WHERE id=?", (rotation_id,)).fetchone()
    return _rotation_dict(row)


@app.put("/api/admin/rotations/{rotation_id}")
def admin_update_rotation(rotation_id: str, body: dict[str, Any] = Body(...), _: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM rotations WHERE id=?", (rotation_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Rotation not found")
        name = body.get("name", row["name"])
        interval = body.get("interval_seconds", row["interval_seconds"])
        active = body.get("active", bool(row["active"]))
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise HTTPException(status_code=422, detail="Rotation name must be 1-100 characters")
        if not isinstance(interval, int) or isinstance(interval, bool) or not 10 <= interval <= 600 or not isinstance(active, bool):
            raise HTTPException(status_code=422, detail="Invalid rotation settings")
        dashboards = _check_dashboard_ids(conn, body.get("dashboard_ids", _json(row["dashboard_ids_json"])))
        conn.execute("UPDATE rotations SET name=?,dashboard_ids_json=?,interval_seconds=?,active=?,updated_at=? WHERE id=?", (name.strip(), json.dumps(dashboards), interval, int(active), now(), rotation_id))
        updated = conn.execute("SELECT * FROM rotations WHERE id=?", (rotation_id,)).fetchone()
    return _rotation_dict(updated)


@app.get("/api/public/rotations/{public_id}")
def public_rotation(public_id: str) -> dict[str, Any]:
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM rotations WHERE public_id=? AND active=1", (public_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="TV rotation not found")
        rotation = _rotation_dict(row)
        dashboards = []
        for dashboard_id in rotation["dashboard_ids"]:
            dashboard = conn.execute("SELECT * FROM dashboards WHERE id=?", (dashboard_id,)).fetchone()
            if dashboard:
                dashboards.append(_dashboard_preview(dashboard))
    return {"name": rotation["name"], "interval_seconds": rotation["interval_seconds"], "dashboards": dashboards}


@app.get("/tv/{public_id}", response_class=HTMLResponse, include_in_schema=False)
def tv_page(public_id: str) -> HTMLResponse:
    with db_connection() as conn:
        row = conn.execute("SELECT id FROM rotations WHERE public_id=? AND active=1", (public_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="TV rotation not found")
    html = (STATIC_DIR / "tv.html").read_text(encoding="utf-8").replace("__PUBLIC_ID__", public_id)
    return HTMLResponse(html)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def admin_console() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
