"""Тесты калькуляционного ядра — по одному на каждую "проверку" из must-have ТЗ
(Кейс: Автоматизация формирования заказов поставщикам, ekt.kz)."""

import copy
import json
from pathlib import Path

import pytest

from calc_engine.pipeline import forecast_month, compute_seasonality_index, process_item

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


# --- Must-have 2: сезонность отражается в прогнозе, а не просто среднее по истории ---

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

    # прогноз на пиковый месяц должен быть заметно выше прогноза на непиковый
    assert forecast_peak > forecast_offpeak * 1.3
    # и не должен схлопываться к простому среднему по всей истории
    assert forecast_peak > flat_average * 1.2
    assert result["_debug"]["seasonality_index"]["07"] > 1.0


# --- Must-have 3: компенсация упущенного спроса в stockout-периоды ---

def test_stockout_increases_estimated_need_vs_raw_sales(fixture_data):
    item = _item(fixture_data, "SKU-STOCKOUT")
    result = process_item(item)

    adjusted = result["_debug"]["monthly_forecast_adjusted"]
    raw = result["_debug"]["monthly_forecast_raw"]

    assert "2025-07" in result["_debug"]["stockout_months"]
    assert "2025-08" in result["_debug"]["stockout_months"]
    # расчёт по скорректированным данным должен давать более высокую потребность,
    # чем расчёт по "сырым" фактическим продажам (которые занижены нулями в stockout)
    assert adjusted > raw


# --- Must-have 4: разовый крупный заказ не должен раздувать регулярную потребность ---

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

    # прогноз с разовым заказом в исходных данных не должен существенно отличаться
    # от прогноза без него - т.к. выброс должен быть отсечён на этапе очистки
    assert forecast_with == pytest.approx(forecast_without, rel=0.05)


# --- Must-have 1: изменение товара в пути отражается на итоговом результате ---

def test_recommendation_sensitive_to_in_transit_qty(fixture_data):
    item_no_transit = _item(fixture_data, "SKU-TRANSIT")
    item_no_transit["in_transit_qty"] = 0
    result_no_transit = process_item(item_no_transit)

    item_with_transit = _item(fixture_data, "SKU-TRANSIT")
    item_with_transit["in_transit_qty"] = 1000
    result_with_transit = process_item(item_with_transit)

    assert result_with_transit["recommended_qty"] < result_no_transit["recommended_qty"]
    assert result_with_transit["recommended_qty"] == 0  # 1000 уже с лихвой покрывает спрос


# --- Must-have 5: каждая строка сопровождается обоснованием ---

def test_every_item_has_non_empty_reason_and_valid_urgency(fixture_data):
    for item in fixture_data["items"]:
        result = process_item(copy.deepcopy(item))
        assert result["reason"], f"Пустое обоснование для {result['sku']}"
        assert result["urgency"] in {"critical", "soon", "planned"}
