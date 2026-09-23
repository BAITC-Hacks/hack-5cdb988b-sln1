"""Backend-эндпоинт: принимает данные, вызывает расчётное ядро, отдаёт Контракт 2/3.

Два пути в систему:
  POST /api/recalculate — принимает уже готовый Контракт 1 (JSON), сразу считает.
                          Нужен для разработки/демо, пока парсер файлов не готов.
  POST /api/upload       — принимает сырые файлы поставщика. Сам парсинг (чтение CSV,
                          определение поставщика/типа файла, извлечение полей) —
                          зона Человека 1, здесь этой логики нет и не будет: эндпоинт
                          только вызывает parser.interface.parse_uploaded_files и
                          передаёт результат в process_supplier.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from calc_engine.pipeline import process_supplier

app = FastAPI(title="ekt.kz procurement recommendations API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # для хакатон-демо; на проде сузить до домена дашборда
    allow_methods=["*"],
    allow_headers=["*"],
)


class SupplierContract1(BaseModel):
    supplier: str
    generated_at: str | None = None
    items: list[dict[str, Any]]


class RecalculateRequest(BaseModel):
    suppliers: list[SupplierContract1]


@app.post("/api/recalculate")
def recalculate(payload: RecalculateRequest) -> dict[str, Any]:
    """Вход — Контракт 1 (по одному или нескольким поставщикам), выход — Контракт 2/3."""
    results = [process_supplier(supplier.model_dump()) for supplier in payload.suppliers]
    for supplier_result in results:
        for item in supplier_result["items"]:
            item.pop("_debug", None)
    return {
        "generated_at": datetime.now(timezone.utc).date().isoformat(),
        "suppliers": results,
    }


@app.post("/api/upload")
def upload(files: list[UploadFile]) -> dict[str, Any]:
    """Принимает файлы от пользователя, парсит их (код Человека 1) и пересчитывает (наш код).

    Парсер подключается сюда: src/parser/interface.py, функция parse_uploaded_files.
    Пока этого модуля нет - явная 501, а не тихая заглушка с придуманными данными.
    """
    try:
        from parser.interface import parse_uploaded_files
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail="Парсер файлов ещё не подключён (src/parser/interface.py отсутствует).",
        ) from exc

    contracts_by_supplier = parse_uploaded_files(files)

    results = []
    errors = []
    for supplier_name, contract1 in contracts_by_supplier.items():
        if "error" in contract1:
            errors.append({"supplier": supplier_name, "error": contract1["error"]})
            continue
        supplier_result = process_supplier(contract1)
        for item in supplier_result["items"]:
            item.pop("_debug", None)
        results.append(supplier_result)

    return {
        "generated_at": datetime.now(timezone.utc).date().isoformat(),
        "suppliers": results,
        "errors": errors,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
