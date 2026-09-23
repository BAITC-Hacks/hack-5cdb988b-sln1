from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from calc_engine.pipeline import process_supplier

from . import storage

EXTRACTOR_URL = os.environ.get("EXTRACTOR_URL", "http://localhost:8080")
REQUIRED_FILE_TYPES = {"sales", "stock", "transit"}

app = FastAPI(title="ekt.kz procurement recommendations API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    results = [process_supplier(supplier.model_dump()) for supplier in payload.suppliers]
    for supplier_result in results:
        for item in supplier_result["items"]:
            item.pop("_debug", None)
    return {
        "generated_at": datetime.now(timezone.utc).date().isoformat(),
        "suppliers": results,
    }


async def call_extractor(file: UploadFile) -> dict[str, Any]:
    content = await file.read()
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{EXTRACTOR_URL}/extract",
            files={"file": (file.filename, content, file.content_type)},
        )
    response.raise_for_status()

    try:
        fragment = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Экстрактор вернул не-JSON ответ для '{file.filename}': {response.text[:200]!r}",
        ) from exc

    if not isinstance(fragment, dict):
        raise HTTPException(
            status_code=502,
            detail=f"Экстрактор вернул не объект для '{file.filename}': {fragment!r}",
        )
    if "error" not in fragment and not {"supplier", "file_type", "items"}.issubset(fragment.keys()):
        raise HTTPException(
            status_code=502,
            detail=(
                f"Экстрактор вернул неожиданный формат для '{file.filename}': "
                f"ожидались поля supplier/file_type/items или error, получено {sorted(fragment.keys())}"
            ),
        )
    return fragment


def _build_contract1_from_storage(supplier: str) -> dict[str, Any] | None:
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
            "name": sales.get("name") or stock.get("name") or transit.get("name"),
            "category": moq.get("category") or transit.get("category"),
            "moq": moq.get("moq", 1),
            "transactions": sales.get("transactions", []),
            "monthly_stock": stock.get("monthly_stock", {}),
            "incoming_shipments": transit.get("incoming_shipments", []),
        })
    return {"supplier": supplier, "items": items}


@app.post("/api/upload")
async def upload(files: list[UploadFile]) -> dict[str, Any]:
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
            upload_errors.append({"supplier": fragment.get("supplier") or file.filename, "error": fragment["error"]})
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


_DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "dashboard")
if os.path.isdir(_DASHBOARD_DIR):
    app.mount("/", StaticFiles(directory=_DASHBOARD_DIR, html=True), name="dashboard")
