"""Детерминированно строит tests/fixtures/mock_input.json — по одному SKU
на каждый must-have проверяемый сценарий из ТЗ (без randomness, чтобы тесты были воспроизводимы).
Запуск: python tests/fixtures/build_fixture.py
"""

import json
from pathlib import Path

MONTHS_2024 = [f"2024-{m:02d}" for m in range(1, 13)]
MONTHS_2025 = [f"2025-{m:02d}" for m in range(1, 13)]
ALL_MONTHS = MONTHS_2024 + MONTHS_2025


def month_to_day(month: str, day: int = 15) -> str:
    return f"{month}-{day:02d}"


def make_seasonal_sku() -> dict:
    """SKU-SEASON: чёткий летний пик (июнь-август x3), остальное — база 20/мес.
    Проверка must-have #2: прогноз на летний месяц должен отражать пик, а не среднее по году.
    """
    base = 20
    peak_months = {"06", "07", "08"}
    transactions = []
    for month in ALL_MONTHS:
        qty = base * 3 if month[5:7] in peak_months else base
        transactions.append({"date": month_to_day(month), "order_id": f"ORD-{month}", "qty": qty})
    monthly_stock = {m: 500 for m in ALL_MONTHS}  # остатков всегда хватало, stockout нет
    return {
        "sku": "SKU-SEASON",
        "name": "Товар с выраженной летней сезонностью",
        "category": "Тест",
        "moq": 1,
        "transactions": transactions,
        "monthly_stock": monthly_stock,
        "in_transit_qty": 0,
    }


def make_stockout_sku() -> dict:
    """SKU-STOCKOUT: стабильные продажи 30/мес, но в июле-августе 2025 остаток = 0
    (значит и продажи в эти месяцы искусственно занижены до 0 - censored demand).
    Проверка must-have #3: скорректированная потребность выше, чем расчёт по сырым продажам.
    """
    stockout_months = {"2025-07", "2025-08"}
    transactions = []
    monthly_stock = {}
    for month in ALL_MONTHS:
        stock = 0 if month in stockout_months else 200
        monthly_stock[month] = stock
        qty = 0 if month in stockout_months else 30  # в stockout физически продать не могли
        if qty > 0:
            transactions.append({"date": month_to_day(month), "order_id": f"ORD-{month}", "qty": qty})
    return {
        "sku": "SKU-STOCKOUT",
        "name": "Товар с дефицитом в июле-августе 2025",
        "category": "Тест",
        "moq": 1,
        "transactions": transactions,
        "monthly_stock": monthly_stock,
        "in_transit_qty": 0,
    }


def make_outlier_sku() -> dict:
    """SKU-OUTLIER: обычные продажи ~15/мес мелкими заказами, но в марте 2025
    один клиент разово купил 900 шт. Проверка must-have #4: этот разовый заказ
    не должен существенно поднимать рекомендуемое регулярное количество.
    """
    transactions = []
    for month in ALL_MONTHS:
        transactions.append({"date": month_to_day(month, 5), "order_id": f"ORD-{month}-A", "qty": 8})
        transactions.append({"date": month_to_day(month, 20), "order_id": f"ORD-{month}-B", "qty": 7})
    transactions.append({"date": "2025-03-10", "order_id": "ORD-BULK-ONEOFF", "qty": 900})
    monthly_stock = {m: 300 for m in ALL_MONTHS}
    return {
        "sku": "SKU-OUTLIER",
        "name": "Товар с разовым крупным заказом в марте 2025",
        "category": "Тест",
        "moq": 1,
        "transactions": transactions,
        "monthly_stock": monthly_stock,
        "in_transit_qty": 0,
    }


def make_transit_sensitivity_sku() -> dict:
    """SKU-TRANSIT: стабильные продажи, остаток низкий. Используется дважды в тесте
    (с in_transit_qty=0 и in_transit_qty=1000) чтобы доказать must-have #1:
    изменение товара в пути отражается на итоговой рекомендации."""
    transactions = [
        {"date": month_to_day(m), "order_id": f"ORD-{m}", "qty": 25} for m in ALL_MONTHS
    ]
    monthly_stock = {m: 10 for m in ALL_MONTHS}
    return {
        "sku": "SKU-TRANSIT",
        "name": "Товар для проверки чувствительности к товару в пути",
        "category": "Тест",
        "moq": 1,
        "transactions": transactions,
        "monthly_stock": monthly_stock,
        "in_transit_qty": 0,
    }


def build() -> dict:
    return {
        "supplier": "TEST_SUPPLIER",
        "generated_at": "2026-01-01",
        "items": [
            make_seasonal_sku(),
            make_stockout_sku(),
            make_outlier_sku(),
            make_transit_sensitivity_sku(),
        ],
    }


if __name__ == "__main__":
    out_path = Path(__file__).parent / "mock_input.json"
    out_path.write_text(json.dumps(build(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written {out_path}")
