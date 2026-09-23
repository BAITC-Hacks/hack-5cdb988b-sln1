import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from api import main as api_main
from api.main import app

client = TestClient(app)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mock_input.json"


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


def test_upload_calls_extractor_and_returns_contract2(monkeypatch):
    """Проверяет реальную связку backend -> экстрактор, без запущенного .NET-сервиса:
    подменяем транспорт httpx так, будто экстрактор ответил валидным Контрактом 1."""
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    one_item_contract1 = {"supplier": "IEK", "items": [fixture["items"][0]]}
    extractor_response = {"IEK": one_item_contract1}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/extract"
        return httpx.Response(200, json=extractor_response)

    transport = httpx.MockTransport(handler)

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(api_main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post("/api/upload", files={"files": ("iek_sales.csv", b"data", "text/csv")})

    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert len(body["suppliers"]) == 1
    assert body["suppliers"][0]["supplier"] == "IEK"
    assert body["suppliers"][0]["items"][0]["sku"] == fixture["items"][0]["sku"]


def test_upload_surfaces_per_supplier_errors_from_extractor(monkeypatch):
    extractor_response = {"IEK": {"error": "не хватает файла 'Товар в пути'"}}

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=extractor_response))

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(api_main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post("/api/upload", files={"files": ("iek_sales.csv", b"data", "text/csv")})

    assert response.status_code == 200
    body = response.json()
    assert body["suppliers"] == []
    assert body["errors"] == [{"supplier": "IEK", "error": "не хватает файла 'Товар в пути'"}]
