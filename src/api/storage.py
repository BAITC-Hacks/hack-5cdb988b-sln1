"""Хранилище последних загруженных фрагментов данных по (поставщик, тип файла, артикул).

Зачем это нужно: юзер грузит файлы не все сразу и не всегда полный набор - сегодня
обновился только "товар в пути" для IEK, через неделю - только "остатки". Каждая
новая загрузка файла определённого типа ПОЛНОСТЬЮ заменяет то, что было известно
по этому типу файла для этого поставщика (не мерджит построчно) - партнёр присылает
актуальный срез, а не дельту. Данные о продажах/остатках/MOQ по другим типам файлов
при этом не трогаются и переживают рестарт backend'а (обычный словарь в памяти - нет).

"Какие данные уже были, какие новые" не требует отдельной логики: INSERT OR REPLACE
по первичному ключу (supplier, file_type, sku) сам решает - есть строка - обновится,
нет - создастся.
"""

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
    """Заменяет все известные по этому (supplier, file_type) артикулы на items.
    Артикулы, которых в новой загрузке нет, но которые были раньше по ЭТОМУ типу
    файла, удаляются - партнёр присылает актуальный срез, а не дельту."""
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
    """{"sales": {sku: {...}}, "stock": {...}, ...} - последнее известное состояние
    по каждому типу файла для этого поставщика, из чего бы оно ни было собрано."""
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
