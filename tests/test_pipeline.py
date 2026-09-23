import copy
import json
from pathlib import Path

import pytest

from calc_engine.pipeline import forecast_month, compute_seasonality_index, process_item, process_supplier

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mock_input.json"


@pytest.fixture(scope="module")
def fixture_data():
    if not FIXTURE_PATH.exists():
        pytest.fail(f"Фикстура не найдена: {FIXTURE_PATH}. Запусти: python tests/fixtures/build_fixture.py")
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _item(fixture_data, sku: str) -> dict:
    for item in fixture_data["items"]:
        if item["sku"] == sku:
            return copy.deepcopy(item)
    raise KeyError(sku)


def test_seasonality_reflects_peak_not_flat_average(fixture_data):
    item = _item(fixture_data, "SKU-SEASON")
    result = process_item(item)

    monthly_sales = {}
    for t in item["transactions"]:
        monthly_sales.setdefault(t["date"][:7], 0)
        monthly_sales[t["date"][:7]] += t["qty"]
    seasonality_index = compute_seasonality_index(monthly_sales)

    forecast_peak = forecast_month(monthly_sales, seasonality_index, target_month="2026-07")
    forecast_offpeak = forecast_month(monthly_sales, seasonality_index, target_month="2026-01")
    flat_average = sum(monthly_sales.values()) / len(monthly_sales)

    assert forecast_peak > forecast_offpeak * 1.3
    assert forecast_peak > flat_average * 1.2
    assert result["_debug"]["seasonality_index"]["07"] > 1.0


def test_stockout_increases_estimated_need_vs_raw_sales(fixture_data):
    item = _item(fixture_data, "SKU-STOCKOUT")
    result = process_item(item)

    adjusted = result["_debug"]["monthly_forecast_adjusted"]
    raw = result["_debug"]["monthly_forecast_raw"]

    assert "2025-07" in result["_debug"]["stockout_months"]
    assert "2025-08" in result["_debug"]["stockout_months"]
    assert adjusted > raw


def test_one_off_bulk_order_excluded_from_regular_demand(fixture_data):
    item_with_outlier = _item(fixture_data, "SKU-OUTLIER")
    result_with_outlier = process_item(item_with_outlier)

    assert result_with_outlier["_debug"]["removed_outlier_orders"] == 1

    item_without_outlier = _item(fixture_data, "SKU-OUTLIER")
    item_without_outlier["transactions"] = [
        t for t in item_without_outlier["transactions"] if t["order_id"] != "ORD-BULK-ONEOFF"
    ]
    result_without_outlier = process_item(item_without_outlier)

    forecast_with = result_with_outlier["_debug"]["monthly_forecast_adjusted"]
    forecast_without = result_without_outlier["_debug"]["monthly_forecast_adjusted"]

    assert forecast_with == pytest.approx(forecast_without, rel=0.05)


def test_recommendation_sensitive_to_in_transit_qty(fixture_data):
    item_no_transit = _item(fixture_data, "SKU-TRANSIT")
    item_no_transit["incoming_shipments"] = []
    result_no_transit = process_item(item_no_transit)

    item_with_transit = _item(fixture_data, "SKU-TRANSIT")
    item_with_transit["incoming_shipments"] = [{"qty": 1000, "expected_date": "2026-01-10"}]
    result_with_transit = process_item(item_with_transit)

    assert result_with_transit["recommended_qty"] < result_no_transit["recommended_qty"]
    assert result_with_transit["recommended_qty"] == 0


def _flat_item(sku: str, category: str, qty: float = 25) -> dict:
    months = [f"2025-{m:02d}" for m in range(1, 13)]
    return {
        "sku": sku,
        "name": sku,
        "category": category,
        "moq": 1,
        "transactions": [{"date": f"{m}-15", "order_id": f"O-{sku}-{m}", "qty": qty} for m in months],
        "monthly_stock": {m: 300 for m in months},
        "incoming_shipments": [],
    }


def _volatile_item(sku: str, category: str) -> dict:
    months = [f"2025-{m:02d}" for m in range(1, 13)]
    return {
        "sku": sku,
        "name": sku,
        "category": category,
        "moq": 1,
        "transactions": [
            {"date": f"{m}-15", "order_id": f"O-{sku}-{m}", "qty": (80 if i % 2 == 0 else 2)}
            for i, m in enumerate(months)
        ],
        "monthly_stock": {m: 300 for m in months},
        "incoming_shipments": [],
    }


def test_category_volatility_changes_recommended_qty():
    target = {
        "sku": "SKU-CATEGORY-TEST",
        "name": "Целевой товар",
        "moq": 1,
        "transactions": [
            {"date": f"2025-{m:02d}-15", "order_id": f"O-target-{m}", "qty": 25} for m in range(1, 13)
        ],
        "monthly_stock": {f"2025-{m:02d}": 10 for m in range(1, 13)},
        "incoming_shipments": [],
    }

    stable_payload = {
        "supplier": "TEST",
        "items": [
            {**target, "category": "Стабильная категория"},
            _flat_item("PEER-A", "Стабильная категория"),
            _flat_item("PEER-B", "Стабильная категория"),
        ],
    }
    volatile_payload = {
        "supplier": "TEST",
        "items": [
            {**target, "category": "Волатильная категория"},
            _volatile_item("PEER-C", "Волатильная категория"),
            _volatile_item("PEER-D", "Волатильная категория"),
        ],
    }

    stable_result = process_supplier(stable_payload)
    volatile_result = process_supplier(volatile_payload)

    stable_qty = next(i for i in stable_result["items"] if i["sku"] == "SKU-CATEGORY-TEST")["recommended_qty"]
    volatile_qty = next(i for i in volatile_result["items"] if i["sku"] == "SKU-CATEGORY-TEST")["recommended_qty"]

    assert volatile_qty > stable_qty


def test_every_item_has_non_empty_reason_and_valid_urgency(fixture_data):
    for item in fixture_data["items"]:
        result = process_item(copy.deepcopy(item))
        assert result["reason"], f"Пустое обоснование для {result['sku']}"
        assert result["urgency"] in {"critical", "soon", "planned"}
