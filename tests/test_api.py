import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api import storage as api_storage
from api.main import app

client = TestClient(app)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mock_input.json"


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Каждый тест получает свою SQLite-базу во временной папке - тесты не видят
    данные друг друга и не оставляют файлы после себя."""
    monkeypatch.setattr(api_storage, "DB_PATH", str(tmp_path / "uploads.db"))
    yield


_REAL_ASYNC_CLIENT = httpx.AsyncClient  # ссылка на настоящий класс до любых подмен ниже


def _patch_extractor(monkeypatch, responses: list[dict]):
    """Подменяет транспорт httpx так, будто экстрактор ответил по очереди этими
    JSON-ответами - по одному на каждый файл, в порядке вызова.

    Наследуемся от _REAL_ASYNC_CLIENT (а не от текущего httpx.AsyncClient) специально:
    если вызвать этот хелпер дважды в одном тесте, второй Fake-класс не должен
    случайно унаследоваться от первого (уже подменённого) - иначе транспорт первого
    вызова "просачивается" во второй и его очередь ответов ломается.
    """
    queue = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/extract"
        return httpx.Response(200, json=next(queue))

    transport = httpx.MockTransport(handler)

    class FakeAsyncClient(_REAL_ASYNC_CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(api_main.httpx, "AsyncClient", FakeAsyncClient)


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_recalculate_returns_valid_contract():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    response = client.post("/api/recalculate", json={"suppliers": [fixture]})

    assert response.status_code == 200
    body = response.json()

    assert "generated_at" in body
    assert isinstance(body["suppliers"], list)
    assert len(body["suppliers"]) == 1

    supplier_result = body["suppliers"][0]
    assert supplier_result["supplier"] == "TEST_SUPPLIER"

    for item in supplier_result["items"]:
        assert "_debug" not in item
        assert item["urgency"] in {"critical", "soon", "planned"}
        assert isinstance(item["reason"], str) and item["reason"]
        assert item["recommended_qty"] >= 0


def test_upload_returns_502_when_extractor_unreachable():
    # EXTRACTOR_URL по умолчанию указывает на localhost:8080, где в тестах никто не слушает
    response = client.post("/api/upload", files={"files": ("test.csv", b"a;b;c\n1;2;3", "text/csv")})
    assert response.status_code == 502


def test_upload_builds_contract_from_multiple_files_in_one_request(monkeypatch):
    _patch_extractor(monkeypatch, [
        {"supplier": "IEK", "file_type": "sales", "items": {
            "SKU1": {"name": "Товар 1", "transactions": [
                {"date": f"2025-{m:02d}-15", "order_id": f"O{m}", "qty": 20} for m in range(1, 13)
            ]},
        }},
        {"supplier": "IEK", "file_type": "stock", "items": {
            "SKU1": {"monthly_stock": {f"2025-{m:02d}": 100 for m in range(1, 13)}},
        }},
        {"supplier": "IEK", "file_type": "transit", "items": {"SKU1": {"in_transit_qty": 0}}},
    ])

    response = client.post(
        "/api/upload",
        files=[
            ("files", ("sales.csv", b"data", "text/csv")),
            ("files", ("stock.csv", b"data", "text/csv")),
            ("files", ("transit.csv", b"data", "text/csv")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert len(body["suppliers"]) == 1
    assert body["suppliers"][0]["supplier"] == "IEK"
    assert body["suppliers"][0]["items"][0]["sku"] == "SKU1"


def test_upload_reports_missing_required_file_types(monkeypatch):
    _patch_extractor(monkeypatch, [
        {"supplier": "NEWSUP", "file_type": "sales", "items": {"SKU1": {"name": "Товар 1", "transactions": []}}},
    ])

    response = client.post("/api/upload", files={"files": ("sales.csv", b"data", "text/csv")})

    assert response.status_code == 200
    body = response.json()
    assert body["suppliers"] == []
    assert body["errors"] == [{"supplier": "NEWSUP", "error": "не хватает файлов: stock, transit"}]


def test_upload_partial_update_reuses_previously_cached_files(monkeypatch):
    """Юзер сначала грузит полный набор, потом - только обновлённый 'товар в пути'.
    Второй запрос не должен требовать заново продажи/остатки - берём их из кэша."""
    _patch_extractor(monkeypatch, [
        {"supplier": "PARTSUP", "file_type": "sales", "items": {
            "SKUX": {"name": "Товар X", "transactions": [
                {"date": f"2025-{m:02d}-15", "order_id": f"O{m}", "qty": 25} for m in range(1, 13)
            ]},
        }},
        {"supplier": "PARTSUP", "file_type": "stock", "items": {
            "SKUX": {"monthly_stock": {f"2025-{m:02d}": 10 for m in range(1, 13)}},
        }},
        {"supplier": "PARTSUP", "file_type": "transit", "items": {"SKUX": {"in_transit_qty": 0}}},
    ])
    first = client.post(
        "/api/upload",
        files=[
            ("files", ("sales.csv", b"data", "text/csv")),
            ("files", ("stock.csv", b"data", "text/csv")),
            ("files", ("transit.csv", b"data", "text/csv")),
        ],
    )
    first_qty = first.json()["suppliers"][0]["items"][0]["recommended_qty"]
    assert first_qty > 0  # остатка мало, спрос стабильный -> должны рекомендовать заказ

    # второй запрос - только обновлённый файл "товар в пути", без продаж и остатков
    _patch_extractor(monkeypatch, [
        {"supplier": "PARTSUP", "file_type": "transit", "items": {"SKUX": {"in_transit_qty": 1000}}},
    ])
    second = client.post("/api/upload", files=[("files", ("transit_update.csv", b"data", "text/csv"))])

    assert second.status_code == 200
    body = second.json()
    assert body["errors"] == []  # продажи/остатки не пропали - взялись из кэша
    second_qty = body["suppliers"][0]["items"][0]["recommended_qty"]
    assert second_qty == 0  # товара в пути теперь с избытком, заказ больше не нужен


def test_upload_surfaces_per_file_errors_from_extractor(monkeypatch):
    _patch_extractor(monkeypatch, [{"error": "не удалось определить поставщика по имени файла"}])

    response = client.post("/api/upload", files={"files": ("непонятный_файл.csv", b"data", "text/csv")})

    assert response.status_code == 200
    body = response.json()
    assert body["suppliers"] == []
    assert body["errors"] == [{"file": "непонятный_файл.csv", "error": "не удалось определить поставщика по имени файла"}]
