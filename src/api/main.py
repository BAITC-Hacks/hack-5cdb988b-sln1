"""Backend-эндпоинт: принимает данные, вызывает расчётное ядро, отдаёт Контракт 2/3.

Два пути в систему:
  POST /api/recalculate — принимает уже готовый Контракт 1 (JSON), сразу считает.
                          Нужен для разработки/демо, пока парсер файлов не готов.
  POST /api/upload       — принимает сырые файлы поставщика, пересылает их отдельному
                          .NET-сервису экстрактора (Человек 1, EXTRACTOR_URL) по HTTP,
                          получает обратно Контракт 1 и считает через process_supplier.
                          Парсинга/классификации файлов здесь нет и не будет - это зона
                          сервиса экстрактора, а не этого backend'а.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from calc_engine.pipeline import process_supplier

EXTRACTOR_URL = os.environ.get("EXTRACTOR_URL", "http://localhost:8080")

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


async def call_extractor(files: list[UploadFile]) -> dict[str, Any]:
    """Пересылает загруженные файлы в сервис экстрактора (Человек 1) и возвращает
    ответ как есть - {"IEK": {...контракт1...}, "SystemElectric": {"error": "..."}, ...}.
    Это единственное место, которое знает про EXTRACTOR_URL - если адрес/протокол
    сервиса поменяется, менять нужно только здесь."""
    multipart_files = [("files", (f.filename, await f.read(), f.content_type)) for f in files]
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(f"{EXTRACTOR_URL}/extract", files=multipart_files)
    response.raise_for_status()
    return response.json()


@app.post("/api/upload")
async def upload(files: list[UploadFile]) -> dict[str, Any]:
    """Принимает файлы от пользователя, отправляет их сервису экстрактора (Человек 1,
    отдельный .NET-контейнер) и пересчитывает результат через process_supplier.

    Ожидаемый ответ экстрактора: {"<поставщик>": {...Контракт 1...} | {"error": "..."}}.
    Если сервис недоступен - явная 502, а не тихая заглушка с придуманными данными.
    """
    try:
        contracts_by_supplier = await call_extractor(files)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Сервис экстрактора недоступен или вернул ошибку ({EXTRACTOR_URL}): {exc}",
        ) from exc

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
