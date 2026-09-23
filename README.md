# HackAlem AI — Track 05: Logistics

## Задание 01 — «Автоматический расчёт заказов поставщикам для пополнения склада»

**Владелец задачи:** ТОО «Электрокомплект»  
**Основной пользователь:** менеджер отдела закупа

Решение автоматизирует подготовку рекомендаций по пополнению склада: пользователь загружает исходные файлы, система рассчитывает рекомендуемые количества по поставщикам, показывает срочность и объяснение, после чего менеджер проверяет и подтверждает результат.

> Заказы поставщикам автоматически не отправляются. Финальное решение остаётся за ответственным сотрудником.

---

## 1. Что должно решать решение

По официальному ТЗ расчёт должен учитывать:

- историю продаж;
- текущие остатки;
- товары в пути;
- категории товаров;
- сезонность;
- устойчивый рост спроса;
- прогноз прироста;
- упущенный спрос в периоды stockout;
- разовые крупные заказы и выбросы;
- поставщиков и сроки поставки.

Целевой сценарий:

```text
загрузка файлов
→ обработка данных
→ расчёт рекомендаций
→ просмотр по поставщикам
→ проверка объяснений
→ корректировка / подтверждение
→ экспорт
```

---

## 2. Current status

В репозитории уже есть:

- dashboard;
- FastAPI backend;
- расчётное ядро;
- SQLite-кэш загруженных фрагментов;
- .NET extractor;
- backend и frontend tests.

Полный end-to-end с реальными Excel-файлами **ещё не завершён**: backend ожидает от extractor canonical fragment вида `supplier + file_type + items`, а текущий `/extract` пока возвращает preview Excel-таблицы в Markdown.

| Компонент | Статус |
|---|---|
| Dashboard | **Implemented** |
| `POST /api/upload` | **Implemented** |
| FastAPI backend | **Implemented** |
| SQLite cache | **Implemented** |
| Расчёт по SKU | **Implemented** |
| Сезонность | **Implemented** |
| Stockout compensation | **Implemented** |
| Учёт текущего остатка | **Implemented** |
| Учёт товара в пути | **Implemented** |
| Базовое исключение крупных выбросов | **Implemented** |
| Supplier grouping | **Implemented** |
| Explanation | **Implemented** |
| Urgency | **Implemented** |
| CSV export | **Implemented** |
| Human approval в UI | **Implemented** |
| Excel extractor | **In progress** |
| End-to-end реальные файлы → рекомендации | **In progress** |
| Customer-level anomaly по `customer_id` | **Not implemented** |
| Устойчивый trend / growth | **Not implemented** |
| Отдельный forecast growth input | **Not implemented** |
| Supplier-specific lead time | **Not implemented** |
| Интеграция с 1С | **Not implemented** |
| Автоматическая отправка поставщику | **Not implemented by design** |

---

## 3. Архитектура

Целевой поток:

```text
Dashboard
   │
   │ POST /api/upload
   ▼
FastAPI backend
   │
   ├─ файл → extractor /extract
   ├─ canonical fragment → SQLite cache
   ├─ сборка данных по поставщику
   └─ process_supplier(...)
            │
            ▼
       JSON recommendations
            │
            ▼
Dashboard
→ таблица
→ фильтры
→ approve
→ CSV export
```

Для расчёта backend ожидает фрагменты типов:

```text
sales
stock
transit
```

Дополнительно поддерживается:

```text
moq
```

---

## 4. API

### `POST /api/upload`

Принимает несколько файлов одним multipart-запросом:

```text
multipart/form-data
files: File[]
```

Backend:

1. отправляет каждый файл в extractor;
2. сохраняет canonical fragments в SQLite;
3. собирает данные по затронутым поставщикам;
4. запускает `process_supplier(...)`;
5. возвращает готовый JSON.

Ожидаемый ответ:

```json
{
  "generated_at": "2026-09-23",
  "suppliers": [
    {
      "supplier": "IEK",
      "items": [
        {
          "sku": "SKU-001",
          "name": "Товар",
          "category": "Категория",
          "current_stock": 20,
          "in_transit_qty": 10,
          "recommended_qty": 55,
          "urgency": "critical",
          "reason": "Краткое объяснение расчёта"
        }
      ]
    }
  ],
  "errors": []
}
```

`urgency`:

```text
critical
soon
planned
```

### `POST /api/recalculate`

Принимает уже нормализованные данные и запускает расчёт без загрузки файлов.

### `GET /api/health`

```json
{
  "status": "ok"
}
```

---

## 5. Методология расчёта

Расчёт выполняется по каждому SKU внутри `process_supplier(...)`.

### 5.1. Исключение крупных разовых транзакций

Используется robust-эвристика:

```text
large transaction:
abs(qty) > median(abs(qty)) × 4
```

Крупные операции исключаются из регулярного спроса только если они редкие:

```text
share of large transactions <= 15%
```

Если крупные продажи повторяются часто, алгоритм не исключает их автоматически.

> Ограничение: отдельный анализ аномалии по обезличенному `customer_id` пока не реализован.

### 5.2. Агрегация спроса

После очистки продажи агрегируются по месяцам:

```text
YYYY-MM → sum(qty)
```

### 5.3. Stockout / упущенный спрос

Stockout определяется только по данным остатков:

```text
monthly_stock <= 0
```

Нулевые продажи сами по себе stockout не означают.

Для stockout-месяца спрос восстанавливается:

1. по среднему спросу того же календарного месяца в другие годы;
2. если такой истории нет — по среднему non-stockout спросу.

### 5.4. Сезонность

Для каждого календарного месяца рассчитывается:

```text
seasonality_index =
average demand for calendar month
/
average demand for all months
```

### 5.5. Forecast

Текущий forecast использует последние `6` месяцев:

```text
recent mean
→ deseasonalization
→ seasonal coefficient target month
```

> Отдельный устойчивый trend / growth factor пока не реализован.

### 5.6. Категория и safety stock

Для категории рассчитывается волатильность:

```text
CV = std(monthly demand) / mean(monthly demand)
```

Страховой запас:

```text
safety_stock_days = 7 × (1 + average category CV)
```

с ограничением:

```text
5 ≤ safety_stock_days ≤ 21
```

### 5.7. Рекомендуемое количество

Текущий default lead time:

```text
30 дней
```

Формула:

```text
daily_forecast = monthly_forecast / 30

demand_for_lead_time =
daily_forecast × lead_time_days

safety_stock =
daily_forecast × safety_stock_days

raw_need =
demand_for_lead_time
+ safety_stock
- current_stock
- in_transit_qty

recommended_qty =
max(0, raw_need)
```

Если задан `MOQ`, заказ округляется вверх до кратности MOQ.

### 5.8. Срочность

По дням покрытия текущим остатком:

```text
< 7 дней   → critical
< 21 дня   → soon
>= 21 дня  → planned
```

---

## 6. Explainability

Для каждой рекомендации формируется `reason`, который может включать:

- прогноз спроса;
- сезонный коэффициент;
- страховой запас;
- категорию;
- stockout-коррекцию;
- исключённые крупные заказы;
- дни покрытия текущим остатком;
- предупреждение об отсутствии актуального остатка.

---

## 7. Dashboard

Dashboard реализован на:

```text
HTML
CSS
JavaScript ES Modules
```

Поддерживаются:

- выбор нескольких файлов;
- drag & drop;
- удаление файлов перед отправкой;
- один синхронный `POST /api/upload`;
- loading state;
- HTTP/network/JSON error states;
- partial errors;
- KPI;
- поиск;
- фильтр поставщика;
- фильтр срочности;
- группировка по поставщикам;
- раскрываемое explanation;
- approve по одной позиции;
- approve видимых позиций поставщика;
- фильтр подтверждённых;
- CSV export.

`dashboard/mock_recommendations.json` используется **только как fixture для frontend-тестов**. Рабочий dashboard его не загружает.

Корневой `mock_recommendations.json` удалён как неиспользуемый.

---

## 8. Структура проекта

```text
.
├── dashboard/
│   ├── index.html
│   ├── styles.css
│   ├── dashboard.mjs
│   ├── recommendations.mjs
│   ├── mock_recommendations.json
│   ├── package.json
│   └── tests/
├── src/
│   ├── api/
│   │   ├── main.py
│   │   └── storage.py
│   ├── calc_engine/
│   │   └── pipeline.py
│   └── extractor/
│       └── Extractor/
├── tests/
│   ├── test_api.py
│   ├── test_pipeline.py
│   └── fixtures/
├── Docs/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## 9. Установка

### Python

Требуется Python 3.11+.

```bash
python -m venv .venv
```

Linux / macOS:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Установка зависимостей:

```bash
pip install -r requirements.txt
```

### Dashboard tests

Для frontend-тестов требуется Node.js.

```bash
npm ci --prefix dashboard
```

### Extractor

Extractor — ASP.NET Core приложение. Для локального запуска требуется .NET SDK либо Docker.

---

## 10. Запуск

### Основной backend + dashboard

FastAPI сам раздаёт содержимое `dashboard/`, поэтому для локального запуска достаточно:

Linux / macOS:

```bash
PYTHONPATH=src uvicorn api.main:app --host 127.0.0.1 --port 8001
```

Windows PowerShell:

```powershell
$env:PYTHONPATH="src"
uvicorn api.main:app --host 127.0.0.1 --port 8001
```

Dashboard:

```text
http://127.0.0.1:8001/
```

Health check:

```text
http://127.0.0.1:8001/api/health
```

Так dashboard и `/api/upload` работают с одного origin.

### Только просмотр dashboard без API

```bash
python3 -m http.server 8000 --bind 127.0.0.1 --directory dashboard
```

Открыть:

```text
http://127.0.0.1:8000/
```

В этом режиме UI доступен, но `/api/upload` отсутствует.

### Docker Compose

```bash
docker compose up --build
```

Сервисы:

```text
extractor : 8080
backend   : 8001
```

Dashboard входит в образ backend и раздаётся через FastAPI.

---

## 11. Environment variables

`.env.example`:

```env
OPENAI_API_KEY=
```

Backend также использует:

```text
EXTRACTOR_URL
UPLOADS_DB_PATH
```

В `docker-compose.yml` они задаются для backend автоматически.

`OPENAI_API_KEY` используется OpenAI-клиентом extractor для field mapping. Текущий `/extract` пока не использует финальный mapping pipeline.

Секреты не должны попадать в Git.

---

## 12. Тестирование

### Backend / calculation engine

```bash
PYTHONPATH=src pytest -q
```

Проверено на текущем архиве:

```text
13 passed
```

### Dashboard

```bash
npm test --prefix dashboard
npm run check --prefix dashboard
```

Проверено на текущем архиве:

```text
19 passed
```

Frontend-тесты покрывают:

- выбор и удаление файлов;
- drag & drop;
- multipart POST;
- loading state;
- защиту от повторной отправки;
- обновление результатов;
- HTTP/network/JSON errors;
- partial errors;
- фильтры;
- approve;
- explanation;
- CSV export.

Backend-тесты покрывают:

- сезонность;
- stockout compensation;
- устойчивость к крупной разовой продаже;
- влияние `in_transit`;
- влияние категории;
- explanation;
- urgency;
- API contracts;
- SQLite cache.

---

## 13. Must Have — текущее покрытие

| Требование | Статус |
|---|---|
| Базовый расчёт по SKU | **Partial / mostly implemented** |
| История продаж | **Implemented** |
| Текущие остатки | **Implemented** |
| Товары в пути | **Implemented** |
| Категория товара | **Implemented** |
| Forecast growth input | **Not implemented** |
| Сезонность | **Implemented** |
| Устойчивый рост спроса | **Not implemented separately** |
| Stockout / lost demand | **Implemented** |
| Крупные разовые заказы | **Implemented at transaction level** |
| Крупная продажа одному клиенту | **Not implemented separately** |
| Supplier grouping | **Implemented** |
| Recommended quantity | **Implemented** |
| Urgency | **Implemented** |
| Explanation | **Implemented** |
| Dashboard / table | **Implemented** |
| Export | **Implemented: CSV** |
| Human approval | **Implemented in dashboard** |
| Автоматическая отправка поставщику | **Intentionally not implemented** |

---

## 14. Ограничения текущего MVP

1. `extractor /extract` пока возвращает preview Excel-таблицы, а backend ожидает canonical fragment — поэтому real-file end-to-end ещё не завершён.
2. Customer-level anomaly detection по обезличенному клиенту отсутствует.
3. Отдельный устойчивый trend / growth отсутствует.
4. Внешний forecast growth не передаётся в расчёт.
5. Lead time сейчас фиксирован значением `30` дней.
6. Нет прямой интеграции с 1С.
7. Approve — UI-состояние, а не размещение заказа у поставщика.
8. OpenAI используется только в extractor field-mapping модуле; расчётное ядро детерминированное и LLM не требует.

---

## 15. Privacy & Security

- клиентские данные должны оставаться обезличенными;
- нельзя использовать неанонимизированные данные клиентов;
- API keys и пароли не должны попадать в Git;
- исходные данные партнёра не должны публиковаться без разрешения;
- `.env` не должен коммититься;
- итоговые рекомендации проверяет ответственный сотрудник;
- заказ не отправляется поставщику автоматически.

---

## 16. Ближайшие TODO

P0 до полного end-to-end:

- [ ] привести `/extract` к canonical fragment contract;
- [ ] прогнать реальные файлы поставщиков через `/api/upload`;
- [ ] выполнить полный smoke test:
  `upload → extract → cache → calculate → JSON → dashboard`.

После P0:

- [ ] customer-level anomaly detection;
- [ ] устойчивый trend / growth;
- [ ] forecast growth input;
- [ ] supplier-specific lead time.
