"""
Расчётное ядро (Человек 2): вход — Контракт 1 (нормализованные данные по поставщику,
собранные парсером из истории продаж, остатков, товара в пути и MOQ),
выход — Контракт 3 (рекомендации по заказам для дашборда).

Пайплайн на SKU:
  1. Отсечение разовых крупных заказов (модифицированный z-score по MAD)
  2. Агрегация очищенных транзакций по месяцам
  3. Компенсация упущенного спроса в месяцы stockout (остаток == 0)
  4. Расчёт сезонного коэффициента по календарным месяцам
  5. Прогноз спроса на следующий месяц (тренд последних месяцев x сезонность)
  6. Рекомендуемое количество = спрос на срок поставки + страховой запас
     - текущий остаток - товар в пути, округлено до кратности MOQ
  7. Текстовое обоснование и уровень срочности
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date
from typing import Any


def _parse_month(date_str: str) -> str:
    return date_str[:7]


def _next_month(month_str: str) -> str:
    year, month = int(month_str[:4]), int(month_str[5:7])
    if month == 12:
        return f"{year + 1}-01"
    return f"{year}-{month + 1:02d}"


def clean_outlier_transactions(
    transactions: list[dict[str, Any]],
    magnitude_multiplier: float = 4.0,
    max_recurring_share: float = 0.15,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Исключает разовые крупные заказы, но не трогает повторяющиеся сезонные всплески.

    Обычный z-score/MAD по всей выборке размеров заказов ломается на сезонных данных:
    если пиковые месяцы дают заметную долю заказов одинаково большого размера (например
    каждое лето), классический MAD может схлопнуться и пометить весь сезонный паттерн
    как выброс. Поэтому критерий — не только "заказ намного больше типичного", но и
    "это единичный/редкий случай", а не повторяющийся из месяца в месяц паттерн.
    """
    qtys = [abs(t["qty"]) for t in transactions]
    if len(qtys) < 5:
        return transactions, []

    median = statistics.median(qtys) or (statistics.mean(qtys) or 1.0)
    threshold = median * magnitude_multiplier

    large = [t for t in transactions if abs(t["qty"]) > threshold]
    if not large:
        return transactions, []

    # если "крупных" заказов много и они составляют заметную долю истории -
    # это регулярный паттерн (сезонность), а не разовый выброс - ничего не убираем
    if len(large) > max_recurring_share * len(transactions):
        return transactions, []

    large_ids = {id(t) for t in large}
    cleaned = [t for t in transactions if id(t) not in large_ids]
    return cleaned, large


def aggregate_monthly(transactions: list[dict[str, Any]]) -> dict[str, float]:
    monthly: dict[str, float] = defaultdict(float)
    for t in transactions:
        monthly[_parse_month(t["date"])] += abs(t["qty"])
    return dict(monthly)


def adjust_for_stockout(
    monthly_sales: dict[str, float], monthly_stock: dict[str, float]
) -> tuple[dict[str, float], set[str]]:
    """Заменяет продажи в месяцы с нулевым остатком на оценку неискажённого спроса
    (среднее по тому же календарному месяцу в другие годы, иначе — общее среднее).
    Без этого stockout-месяцы тянут прогноз вниз, хотя реальный спрос был выше.
    """
    stockout_months = {m for m, stock in monthly_stock.items() if stock is not None and stock <= 0}

    by_calendar_month: dict[str, list[float]] = defaultdict(list)
    for m, v in monthly_sales.items():
        if m not in stockout_months:
            by_calendar_month[m[5:7]].append(v)

    non_stockout_values = [v for m, v in monthly_sales.items() if m not in stockout_months]
    overall_avg = statistics.mean(non_stockout_values) if non_stockout_values else 0.0

    adjusted = dict(monthly_sales)
    for m in stockout_months:
        cal = m[5:7]
        candidates = by_calendar_month.get(cal)
        adjusted[m] = statistics.mean(candidates) if candidates else overall_avg
    return adjusted, stockout_months


def compute_seasonality_index(monthly_sales: dict[str, float]) -> dict[str, float]:
    """Индекс сезонности по календарному месяцу = среднее за этот месяц / среднее за год."""
    by_calendar_month: dict[str, list[float]] = defaultdict(list)
    for m, v in monthly_sales.items():
        by_calendar_month[m[5:7]].append(v)

    overall_avg = statistics.mean(monthly_sales.values()) if monthly_sales else 0.0
    index = {}
    for cal in (f"{i:02d}" for i in range(1, 13)):
        values = by_calendar_month.get(cal)
        index[cal] = (statistics.mean(values) / overall_avg) if values and overall_avg > 0 else 1.0
    return index


def forecast_month(
    monthly_sales: dict[str, float],
    seasonality_index: dict[str, float],
    target_month: str,
    recent_n: int = 6,
) -> float:
    """Прогноз спроса на target_month = тренд последних N месяцев, де-сезонализированный
    и пересезонализированный под целевой месяц (а не просто среднее по всей истории)."""
    months_sorted = sorted(monthly_sales.keys())
    recent_months = months_sorted[-recent_n:]
    if not recent_months:
        return 0.0

    recent_values = [monthly_sales[m] for m in recent_months]
    base = statistics.mean(recent_values)

    recent_seasonal_avg = statistics.mean(seasonality_index[m[5:7]] for m in recent_months)
    deseasonalized_base = base / recent_seasonal_avg if recent_seasonal_avg else base

    target_seasonal = seasonality_index.get(target_month[5:7], 1.0)
    return max(deseasonalized_base * target_seasonal, 0.0)


def build_reason(
    *,
    monthly_forecast: float,
    seasonal_index: float,
    stockout_months: set[str],
    removed_orders: list[dict[str, Any]],
    days_of_stock: float,
) -> str:
    parts = [f"Прогноз спроса ~{monthly_forecast:.0f} шт/мес (сезонный коэф. ×{seasonal_index:.2f})"]
    if stockout_months:
        parts.append(f"скорректировано на дефицит в {len(stockout_months)} мес. ({', '.join(sorted(stockout_months))})")
    if removed_orders:
        total_excluded = sum(abs(o["qty"]) for o in removed_orders)
        parts.append(f"исключено {len(removed_orders)} разовых крупных заказов ({total_excluded:.0f} шт) из расчёта регулярного спроса")
    if days_of_stock == float("inf"):
        parts.append("текущего спроса по товару не зафиксировано")
    else:
        parts.append(f"текущего остатка хватит на {days_of_stock:.0f} дн.")
    return "; ".join(parts) + "."


def compute_urgency(days_of_stock: float) -> str:
    if days_of_stock < 7:
        return "critical"
    if days_of_stock < 21:
        return "soon"
    return "planned"


def process_item(
    item: dict[str, Any],
    *,
    lead_time_days: int = 30,
    safety_stock_days: int = 7,
    reference_month: str | None = None,
) -> dict[str, Any]:
    transactions = item.get("transactions", [])
    monthly_stock = item.get("monthly_stock", {}) or {}

    cleaned_tx, removed_tx = clean_outlier_transactions(transactions)
    raw_monthly_sales = aggregate_monthly(transactions)
    # "сырой" расчёт (для сравнения) должен видеть буквальный 0 продаж в месяцы дефицита,
    # а не тихо пропускать месяц - именно так выглядит наивный расчёт по факту продаж
    for m in monthly_stock:
        raw_monthly_sales.setdefault(m, 0.0)
    monthly_sales = aggregate_monthly(cleaned_tx)
    adjusted_sales, stockout_months = adjust_for_stockout(monthly_sales, monthly_stock)
    seasonality_index = compute_seasonality_index(adjusted_sales)

    known_months = sorted(set(list(monthly_sales.keys()) + list(monthly_stock.keys())))
    last_month = reference_month or (known_months[-1] if known_months else None)
    target_month = _next_month(last_month) if last_month else None

    monthly_forecast = forecast_month(adjusted_sales, seasonality_index, target_month) if target_month else 0.0
    raw_monthly_forecast = forecast_month(
        raw_monthly_sales, compute_seasonality_index(raw_monthly_sales), target_month
    ) if target_month else 0.0

    daily_forecast = monthly_forecast / 30.0
    demand_for_lead_time = daily_forecast * lead_time_days
    safety_stock = daily_forecast * safety_stock_days

    current_stock = monthly_stock.get(last_month, 0) if last_month else 0
    in_transit_qty = item.get("in_transit_qty", 0) or 0

    raw_need = demand_for_lead_time + safety_stock - current_stock - in_transit_qty
    moq = item.get("moq") or 1
    recommended_qty = 0 if raw_need <= 0 else math.ceil(raw_need / moq) * moq

    days_of_stock = (current_stock / daily_forecast) if daily_forecast > 0 else float("inf")
    urgency = compute_urgency(days_of_stock)

    reason = build_reason(
        monthly_forecast=monthly_forecast,
        seasonal_index=seasonality_index.get(target_month[5:7], 1.0) if target_month else 1.0,
        stockout_months=stockout_months,
        removed_orders=removed_tx,
        days_of_stock=days_of_stock,
    )

    return {
        "sku": item["sku"],
        "name": item.get("name", ""),
        "category": item.get("category"),
        "current_stock": current_stock,
        "in_transit_qty": in_transit_qty,
        "recommended_qty": recommended_qty,
        "urgency": urgency,
        "reason": reason,
        # отладочные поля - не часть контракта с фронтом, но полезны для проверки must-have на демо
        "_debug": {
            "monthly_forecast_adjusted": round(monthly_forecast, 2),
            "monthly_forecast_raw": round(raw_monthly_forecast, 2),
            "removed_outlier_orders": len(removed_tx),
            "stockout_months": sorted(stockout_months),
            "seasonality_index": {k: round(v, 2) for k, v in seasonality_index.items()},
        },
    }


def process_supplier(supplier_payload: dict[str, Any], **kwargs) -> dict[str, Any]:
    items = [process_item(item, **kwargs) for item in supplier_payload.get("items", [])]
    return {"supplier": supplier_payload["supplier"], "items": items}
