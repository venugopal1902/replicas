from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from .auth import hash_api_key, hash_password
from .config import settings

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY CHECK (id = 1), email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, expires_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dataset_api_credentials (
    id INTEGER PRIMARY KEY CHECK (id = 1), key_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY, fields_json TEXT NOT NULL, unique_by_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dataset_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    record_json TEXT NOT NULL, unique_key TEXT, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS dataset_records_unique_key
    ON dataset_records(dataset_id, unique_key) WHERE unique_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS dataset_records_order ON dataset_records(dataset_id, id);
CREATE TABLE IF NOT EXISTS dashboards (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, widgets_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rotations (
    id TEXT PRIMARY KEY, public_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    dashboard_ids_json TEXT NOT NULL, interval_seconds INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


@contextmanager
def db_connection() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db_connection() as conn:
        conn.executescript(SCHEMA)
        user = conn.execute("SELECT id FROM admin_users WHERE id = 1").fetchone()
        key_row = conn.execute("SELECT id FROM dataset_api_credentials WHERE id = 1").fetchone()
        if user is None:
            if not settings.admin_password:
                raise RuntimeError(
                    "First boot requires GECKOBOARD_ADMIN_PASSWORD. Set it in the process environment, then restart."
                )
            conn.execute(
                "INSERT INTO admin_users (id,email,display_name,password_hash,created_at) VALUES (1,?,?,?,?)",
                (settings.admin_email, settings.admin_name, hash_password(settings.admin_password), now()),
            )
        if key_row is None:
            if not settings.dataset_api_key:
                raise RuntimeError(
                    "First boot requires GECKOBOARD_DATASET_API_KEY. Set it in the process environment, then restart."
                )
            conn.execute(
                "INSERT INTO dataset_api_credentials (id,key_hash,created_at) VALUES (1,?,?)",
                (hash_api_key(settings.dataset_api_key), now()),
            )
        if settings.seed_demo:
            seed_demo(conn)


def _insert_dataset(conn: sqlite3.Connection, dataset_id: str, fields: dict[str, Any], unique_by: list[str]) -> None:
    stamp = now()
    conn.execute(
        "INSERT OR IGNORE INTO datasets (id,fields_json,unique_by_json,created_at,updated_at) VALUES (?,?,?,?,?)",
        (dataset_id, json.dumps(fields, separators=(",", ":")), json.dumps(unique_by), stamp, stamp),
    )


def _insert_records(
    conn: sqlite3.Connection, dataset_id: str, records: list[dict[str, Any]], unique_by: list[str]
) -> None:
    for record in records:
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


def seed_demo(conn: sqlite3.Connection | None = None) -> None:
    if conn is None:
        with db_connection() as owned:
            seed_demo(owned)
        return
    support_fields = {
        "date": {"type": "date", "name": "Date", "optional": False},
        "team": {"type": "string", "name": "Team", "optional": False},
        "open_tickets": {"type": "number", "name": "Open tickets", "optional": False},
    }
    sales_fields = {
        "date": {"type": "date", "name": "Date", "optional": False},
        "revenue": {"type": "money", "name": "Revenue", "currency_code": "USD", "optional": False},
        "orders": {"type": "number", "name": "Orders", "optional": False},
    }
    _insert_dataset(conn, "support.tickets", support_fields, ["date", "team"])
    _insert_dataset(conn, "sales.daily", sales_fields, ["date"])
    _insert_records(conn, "support.tickets", [
        {"date": "2026-05-18", "team": "North", "open_tickets": 24},
        {"date": "2026-05-18", "team": "South", "open_tickets": 17},
        {"date": "2026-05-19", "team": "North", "open_tickets": 21},
        {"date": "2026-05-19", "team": "South", "open_tickets": 14},
    ], ["date", "team"])
    _insert_records(conn, "sales.daily", [
        {"date": "2026-05-18", "revenue": 12450.0, "orders": 91},
        {"date": "2026-05-19", "revenue": 14820.5, "orders": 106},
        {"date": "2026-05-20", "revenue": 13770.25, "orders": 98},
    ], ["date"])
    stamp = now()
    dashboards = [
        ("support-dashboard", "Support pulse", [
            {"id": "ticket-total", "type": "number", "title": "Open tickets", "dataset_id": "support.tickets", "metric_field": "open_tickets", "aggregate": "sum"},
            {"id": "ticket-trend", "type": "line", "title": "Ticket trend", "dataset_id": "support.tickets", "metric_field": "open_tickets", "time_field": "date", "aggregate": "sum"},
            {"id": "team-leaderboard", "type": "leaderboard", "title": "By team", "dataset_id": "support.tickets", "metric_field": "open_tickets", "dimension_field": "team", "aggregate": "sum"},
        ]),
        ("sales-dashboard", "Revenue pulse", [
            {"id": "revenue-total", "type": "number", "title": "Revenue", "dataset_id": "sales.daily", "metric_field": "revenue", "aggregate": "sum"},
            {"id": "orders-by-day", "type": "column", "title": "Orders by day", "dataset_id": "sales.daily", "metric_field": "orders", "dimension_field": "date", "aggregate": "sum"},
            {"id": "sales-table", "type": "table", "title": "Daily records", "dataset_id": "sales.daily", "limit": 8},
        ]),
    ]
    for dashboard_id, name, widgets in dashboards:
        conn.execute(
            "INSERT OR IGNORE INTO dashboards (id,name,widgets_json,created_at,updated_at) VALUES (?,?,?,?,?)",
            (dashboard_id, name, json.dumps(widgets, separators=(",", ":")), stamp, stamp),
        )
    if not conn.execute("SELECT id FROM rotations WHERE name='Demo screen loop'").fetchone():
        conn.execute(
            "INSERT INTO rotations (id,public_id,name,dashboard_ids_json,interval_seconds,active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("demo-rotation", secrets.token_urlsafe(12), "Demo screen loop", json.dumps(["support-dashboard", "sales-dashboard"]), 20, 1, stamp, stamp),
        )
