from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any


def _parse_month(date_str: str) -> str:
    return date_str[:7]


def _next_month(month_str: str) -> str:
    year, month = int(month_str[:4]), int(month_str[5:7])
    if month == 12:
        return f"{year + 1}-01"
    return f"{year}-{month + 1:02d}"


def _month_start(month_str: str) -> date:
    year, month = int(month_str[:4]), int(month_str[5:7])
    return date(year, month, 1)


def _parse_date(date_str: str) -> date:
    year, month, day = (int(x) for x in date_str.split("-"))
    return date(year, month, day)


def clean_outlier_transactions(
    transactions: list[dict[str, Any]],
    magnitude_multiplier: float = 4.0,
    max_recurring_share: float = 0.15,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    qtys = [abs(t["qty"]) for t in transactions]
    if len(qtys) < 5:
        return transactions, []

    median = statistics.median(qtys) or (statistics.mean(qtys) or 1.0)
    threshold = median * magnitude_multiplier

    large = [t for t in transactions if abs(t["qty"]) > threshold]
    if not large:
        return transactions, []

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


def simulate_incoming_shipments(
    current_stock: float,
    daily_forecast: float,
    shipments: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    if daily_forecast <= 0:
        return {"stockout_date": None, "next_shipment_date": None, "deficit_days": 0}

    events = sorted(
        (
            {"date": _parse_date(s["expected_date"]), "qty": s["qty"]}
            for s in shipments
            if s.get("expected_date")
        ),
        key=lambda e: e["date"],
    )

    remaining = current_stock
    cursor = today
    for event in events:
        if event["date"] <= cursor:
            remaining += event["qty"]
            continue
        days_available = (event["date"] - cursor).days
        stock_needed = daily_forecast * days_available
        if remaining < stock_needed:
            days_to_stockout = remaining / daily_forecast
            stockout_date = cursor + timedelta(days=days_to_stockout)
            deficit_days = (event["date"] - stockout_date).days
            return {
                "stockout_date": stockout_date,
                "next_shipment_date": event["date"],
                "deficit_days": max(deficit_days, 0),
            }
        remaining -= stock_needed
        remaining += event["qty"]
        cursor = event["date"]

    days_of_runway = remaining / daily_forecast
    stockout_date = cursor + timedelta(days=days_of_runway)
    return {"stockout_date": stockout_date, "next_shipment_date": None, "deficit_days": 0}


def build_reason(
    *,
    monthly_forecast: float,
    seasonal_index: float,
    stockout_months: set[str],
    removed_orders: list[dict[str, Any]],
    stockout_sim: dict[str, Any],
    stock_month_is_missing: bool,
    safety_stock_days: float,
    category: str | None,
) -> str:
    parts = [f"Прогноз спроса ~{monthly_forecast:.0f} шт/мес (сезонный коэф. ×{seasonal_index:.2f})"]
    category_note = f" по категории «{category}»" if category else ""
    parts.append(f"страховой запас {safety_stock_days:.0f} дн. (по волатильности спроса{category_note})")
    if stockout_months:
        parts.append(f"скорректировано на дефицит в {len(stockout_months)} мес. ({', '.join(sorted(stockout_months))})")
    if removed_orders:
        total_excluded = sum(abs(o["qty"]) for o in removed_orders)
        parts.append(f"исключено {len(removed_orders)} разовых крупных заказов ({total_excluded:.0f} шт) из расчёта регулярного спроса")

    stockout_date = stockout_sim["stockout_date"]
    if stockout_date is None:
        parts.append("текущего спроса по товару не зафиксировано")
    elif stockout_sim["deficit_days"] > 0:
        parts.append(
            f"запаса хватит до {stockout_date.isoformat()}, ближайшая поставка ожидается "
            f"{stockout_sim['next_shipment_date'].isoformat()} - вероятен дефицит на {stockout_sim['deficit_days']} дн."
        )
    else:
        parts.append(f"запаса (с учётом товара в пути) хватит до {stockout_date.isoformat()}")

    if stock_month_is_missing:
        parts.append("ВНИМАНИЕ: остаток за текущий месяц не указан в отчёте, принят за 0 - проверьте вручную")
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
    incoming_shipments = item.get("incoming_shipments", []) or []

    cleaned_tx, removed_tx = clean_outlier_transactions(transactions)
    raw_monthly_sales = aggregate_monthly(transactions)
    for m in monthly_stock:
        raw_monthly_sales.setdefault(m, 0.0)
    monthly_sales = aggregate_monthly(cleaned_tx)
    adjusted_sales, stockout_months = adjust_for_stockout(monthly_sales, monthly_stock)
    seasonality_index = compute_seasonality_index(adjusted_sales)

    known_months = sorted(set(list(monthly_sales.keys()) + list(monthly_stock.keys())))
    last_month = reference_month or (known_months[-1] if known_months else None)
    target_month = _next_month(last_month) if last_month else None
    today = _month_start(target_month) if target_month else None

    monthly_forecast = forecast_month(adjusted_sales, seasonality_index, target_month) if target_month else 0.0
    raw_monthly_forecast = forecast_month(
        raw_monthly_sales, compute_seasonality_index(raw_monthly_sales), target_month
    ) if target_month else 0.0

    daily_forecast = monthly_forecast / 30.0
    demand_for_lead_time = daily_forecast * lead_time_days
    safety_stock = daily_forecast * safety_stock_days

    current_stock = monthly_stock.get(last_month, 0) if last_month else 0
    stock_month_is_missing = bool(last_month and last_month not in monthly_stock)

    lead_time_end = today + timedelta(days=lead_time_days) if today else None
    qty_arriving_in_lead_time = sum(
        s["qty"] for s in incoming_shipments
        if today and s.get("expected_date") and today <= _parse_date(s["expected_date"]) <= lead_time_end
    )
    total_in_transit_qty = sum(s["qty"] for s in incoming_shipments)

    raw_need = demand_for_lead_time + safety_stock - current_stock - qty_arriving_in_lead_time
    moq = item.get("moq") or 1
    recommended_qty = 0 if raw_need <= 0 else math.ceil(raw_need / moq) * moq

    stockout_sim = (
        simulate_incoming_shipments(current_stock, daily_forecast, incoming_shipments, today)
        if today else {"stockout_date": None, "next_shipment_date": None, "deficit_days": 0}
    )
    days_of_stock = (stockout_sim["stockout_date"] - today).days if stockout_sim["stockout_date"] else float("inf")
    urgency = compute_urgency(days_of_stock)

    reason = build_reason(
        monthly_forecast=monthly_forecast,
        seasonal_index=seasonality_index.get(target_month[5:7], 1.0) if target_month else 1.0,
        stockout_months=stockout_months,
        removed_orders=removed_tx,
        stockout_sim=stockout_sim,
        stock_month_is_missing=stock_month_is_missing,
        safety_stock_days=safety_stock_days,
        category=item.get("category"),
    )

    return {
        "sku": item["sku"],
        "name": item.get("name") or item["sku"],
        "category": item.get("category") or "Без категории",
        "current_stock": current_stock,
        "in_transit_qty": total_in_transit_qty,
        "recommended_qty": recommended_qty,
        "urgency": urgency,
        "reason": reason,
        "_debug": {
            "monthly_forecast_adjusted": round(monthly_forecast, 2),
            "monthly_forecast_raw": round(raw_monthly_forecast, 2),
            "removed_outlier_orders": len(removed_tx),
            "stockout_months": sorted(stockout_months),
            "seasonality_index": {k: round(v, 2) for k, v in seasonality_index.items()},
            "stockout_date": stockout_sim["stockout_date"].isoformat() if stockout_sim["stockout_date"] else None,
            "deficit_days": stockout_sim["deficit_days"],
        },
    }


def _monthly_sales_cv(item: dict[str, Any]) -> float:
    cleaned_tx, _ = clean_outlier_transactions(item.get("transactions", []))
    values = list(aggregate_monthly(cleaned_tx).values())
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    if mean == 0:
        return 0.0
    return statistics.pstdev(values) / mean


def compute_category_safety_days(
    items: list[dict[str, Any]], base_days: float = 7.0, min_days: float = 5.0, max_days: float = 21.0
) -> dict[Any, float]:
    cv_by_category: dict[Any, list[float]] = {}
    for item in items:
        cv_by_category.setdefault(item.get("category"), []).append(_monthly_sales_cv(item))

    safety_days_by_category = {}
    for category, cvs in cv_by_category.items():
        avg_cv = statistics.mean(cvs) if cvs else 0.0
        days = base_days * (1 + avg_cv)
        safety_days_by_category[category] = min(max(days, min_days), max_days)
    return safety_days_by_category


def process_supplier(supplier_payload: dict[str, Any], **kwargs) -> dict[str, Any]:
    items_data = supplier_payload.get("items", [])
    category_safety_days = compute_category_safety_days(items_data)

    items = []
    for item in items_data:
        item_kwargs = dict(kwargs)
        item_kwargs.setdefault("safety_stock_days", category_safety_days.get(item.get("category"), 7.0))
        items.append(process_item(item, **item_kwargs))
    return {"supplier": supplier_payload["supplier"], "items": items}
