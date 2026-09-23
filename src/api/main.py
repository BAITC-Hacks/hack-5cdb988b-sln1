"""Backend-эндпоинт: принимает данные, вызывает расчётное ядро, отдаёт Контракт 2/3.

Два пути в систему:
  POST /api/recalculate — принимает уже готовый Контракт 1 (JSON), сразу считает.
                          Нужен для разработки/демо, пока парсер файлов не готов.
  POST /api/upload       — принимает один или несколько файлов поставщика. Каждый файл
                          отправляется ОТДЕЛЬНО в сервис экстрактора (Человек 1,
                          .NET, EXTRACTOR_URL) - его текущий /extract и так рассчитан
                          на один файл за раз, менять это не нужно.

Зачем нужно хранилище последних файлов по поставщику: юзер не обязан каждый раз
заново грузить все файлы разом. Если сегодня обновился только "Товар в пути" для
IEK, юзер загружает только его - а данные о продажах/остатках/MOQ берутся из того,
что было загружено последний раз (см. storage.py, SQLite - переживает рестарт
backend'а). Без этого любой частичный апдейт стирал бы всё остальное, и юзера
пришлось бы заставлять каждый раз грузить 25-мегабайтную историю продаж заново
ради одного файла с товаром в пути.

Парсинга/классификации самого содержимого файлов здесь нет и не будет - это зона
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

from . import storage

EXTRACTOR_URL = os.environ.get("EXTRACTOR_URL", "http://localhost:8080")
REQUIRED_FILE_TYPES = {"sales", "stock", "transit"}

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


async def call_extractor(file: UploadFile) -> dict[str, Any]:
    """Отправляет ОДИН файл в сервис экстрактора. Ожидаемый ответ:
    {"supplier": "IEK", "file_type": "sales"|"stock"|"transit"|"moq",
     "items": {"<sku>": {...поля этого типа файла...}}}
    или {"error": "..."}, если файл не удалось классифицировать/распарсить."""
    content = await file.read()
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{EXTRACTOR_URL}/extract",
            files={"file": (file.filename, content, file.content_type)},
        )
    response.raise_for_status()
    return response.json()


def _build_contract1_from_storage(supplier: str) -> dict[str, Any] | None:
    """None, если после мерджа со всей историей загрузок всё ещё не хватает
    обязательного типа файла - вызывающий код превращает это в явную ошибку."""
    supplier_cache = storage.get_supplier_fragments(supplier)
    if not REQUIRED_FILE_TYPES.issubset(supplier_cache.keys()):
        return None

    all_skus = {sku for fragment in supplier_cache.values() for sku in fragment}
    items = []
    for sku in all_skus:
        sales = supplier_cache.get("sales", {}).get(sku, {})
        stock = supplier_cache.get("stock", {}).get(sku, {})
        transit = supplier_cache.get("transit", {}).get(sku, {})
        moq = supplier_cache.get("moq", {}).get(sku, {})
        items.append({
            "sku": sku,
            "name": sales.get("name") or stock.get("name") or transit.get("name") or "",
            "category": moq.get("category") or transit.get("category"),
            "moq": moq.get("moq", 1),
            "transactions": sales.get("transactions", []),
            "monthly_stock": stock.get("monthly_stock", {}),
            "in_transit_qty": transit.get("in_transit_qty", 0),
        })
    return {"supplier": supplier, "items": items}


@app.post("/api/upload")
async def upload(files: list[UploadFile]) -> dict[str, Any]:
    """Принимает один или несколько файлов, каждый уходит в экстрактор отдельным
    запросом. Результат мерджится в кэш по (поставщик, тип файла), пересчёт идёт
    по всем поставщикам, которых затронула эта загрузка - с учётом того, что было
    загружено раньше, а не только того, что пришло сейчас."""
    affected_suppliers: set[str] = set()
    upload_errors = []

    for file in files:
        try:
            fragment = await call_extractor(file)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Сервис экстрактора недоступен или вернул ошибку ({EXTRACTOR_URL}): {exc}",
            ) from exc

        if "error" in fragment:
            upload_errors.append({"file": file.filename, "error": fragment["error"]})
            continue

        storage.upsert_fragment(fragment["supplier"], fragment["file_type"], fragment["items"])
        affected_suppliers.add(fragment["supplier"])

    results = []
    errors = list(upload_errors)
    for supplier_name in affected_suppliers:
        contract1 = _build_contract1_from_storage(supplier_name)
        if contract1 is None:
            have = set(storage.get_supplier_fragments(supplier_name).keys())
            missing = REQUIRED_FILE_TYPES - have
            errors.append({"supplier": supplier_name, "error": f"не хватает файлов: {', '.join(sorted(missing))}"})
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
