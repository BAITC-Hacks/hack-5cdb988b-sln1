from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

DB_PATH = os.environ.get("UPLOADS_DB_PATH", "data/uploads.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_fragments (
    supplier   TEXT NOT NULL,
    file_type  TEXT NOT NULL,
    sku        TEXT NOT NULL,
    fields     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (supplier, file_type, sku)
);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_fragment(supplier: str, file_type: str, items: dict[str, dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "DELETE FROM file_fragments WHERE supplier = ? AND file_type = ?",
            (supplier, file_type),
        )
        conn.executemany(
            "INSERT INTO file_fragments (supplier, file_type, sku, fields, updated_at) VALUES (?, ?, ?, ?, ?)",
            [(supplier, file_type, sku, json.dumps(fields, ensure_ascii=False), now) for sku, fields in items.items()],
        )


def get_supplier_fragments(supplier: str) -> dict[str, dict[str, dict[str, Any]]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT file_type, sku, fields FROM file_fragments WHERE supplier = ?",
            (supplier,),
        ).fetchall()

    result: dict[str, dict[str, dict[str, Any]]] = {}
    for file_type, sku, fields_json in rows:
        result.setdefault(file_type, {})[sku] = json.loads(fields_json)
    return result


def known_suppliers() -> set[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT DISTINCT supplier FROM file_fragments").fetchall()
    return {row[0] for row in rows}
