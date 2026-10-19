import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from app.config import DB_PATH


def ensure_parent() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    ensure_parent()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db() -> None:
    ensure_parent()
    with closing(get_connection()) as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS vendors (
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                legal_name TEXT,
                supplier_id TEXT,
                country TEXT,
                business_email TEXT,
                contact_phone TEXT,
                status TEXT DEFAULT 'submitted',
                trusted_bank TEXT,
                risk_flags TEXT,
                metadata TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS vendor_cases (
                id TEXT PRIMARY KEY,
                vendor_id TEXT,
                legal_name TEXT,
                supplier_id TEXT,
                country TEXT,
                business_email TEXT,
                contact_phone TEXT,
                status TEXT,
                documents TEXT,
                extracted TEXT,
                checks TEXT,
                evidence_pack TEXT,
                audit TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY,
                vendor_case_id TEXT,
                vendor_id TEXT,
                invoice_number TEXT,
                amount REAL,
                currency TEXT,
                bank_details TEXT,
                purchase_order TEXT,
                status TEXT,
                checks TEXT,
                raw TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS bank_changes (
                id TEXT PRIMARY KEY,
                vendor_name TEXT,
                trusted_phone TEXT,
                requested_bank TEXT,
                status TEXT,
                guard_results TEXT,
                metadata TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY,
                entity_type TEXT,
                entity_id TEXT,
                action TEXT,
                payload TEXT,
                created_at TEXT
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS scenarios (
                id TEXT PRIMARY KEY,
                name TEXT,
                expected_outcome TEXT,
                status TEXT,
                details TEXT,
                created_at TEXT
            )
            '''
        )
        conn.commit()


def json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = {key: row[key] for key in row.keys()}
    for key in ('trusted_bank', 'risk_flags', 'metadata', 'documents', 'extracted', 'checks', 'evidence_pack', 'audit', 'bank_details', 'requested_bank', 'guard_results', 'raw', 'payload', 'details'):
        if key in data and data[key] is not None:
            try:
                data[key] = json.loads(data[key])
            except (TypeError, ValueError):
                pass
    return data


def insert_audit_event(entity_type: str, entity_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            'INSERT INTO audit_events(id, entity_type, entity_id, action, payload, created_at) VALUES (?, ?, ?, ?, ?, datetime("now"))',
            (f'{entity_type}-{entity_id}-{action}', entity_type, entity_id, action, json_dumps(payload or {})),
        )
        conn.commit()


def fetch_all(table: str, order_by: str = 'created_at DESC') -> list[dict[str, Any]]:
    with closing(get_connection()) as conn:
        rows = conn.execute(f'SELECT * FROM {table} ORDER BY {order_by}').fetchall()
        return [row_to_dict(row) for row in rows]


def fetch_by_id(table: str, record_id: str) -> dict[str, Any] | None:
    with closing(get_connection()) as conn:
        row = conn.execute(f'SELECT * FROM {table} WHERE id = ?', (record_id,)).fetchone()
        if row is None:
            return None
        return row_to_dict(row)


def fetch_vendor_cases() -> list[dict[str, Any]]:
    with closing(get_connection()) as conn:
        rows = conn.execute('SELECT * FROM vendor_cases ORDER BY created_at DESC').fetchall()
        return [row_to_dict(row) for row in rows]


def fetch_invoices() -> list[dict[str, Any]]:
    with closing(get_connection()) as conn:
        rows = conn.execute('SELECT * FROM invoices ORDER BY created_at DESC').fetchall()
        return [row_to_dict(row) for row in rows]
