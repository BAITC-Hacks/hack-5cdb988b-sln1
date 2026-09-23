import json
from pathlib import Path

from fastapi.testclient import TestClient

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


def test_upload_returns_501_without_parser_module():
    response = client.post("/api/upload", files={"files": ("test.csv", b"a;b;c\n1;2;3", "text/csv")})
    assert response.status_code == 501
