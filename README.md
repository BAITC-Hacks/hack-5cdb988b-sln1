# HackAlem AI — Track 05: Logistics

## Задание 01 — «Автоматический расчёт заказов поставщикам для пополнения склада»

**Владелец задачи:** ТОО «Электрокомплект»  
**Основной пользователь:** менеджер отдела закупа

Решение предназначено для автоматизации расчёта пополнения склада: пользователь загружает исходные файлы, система рассчитывает рекомендации по поставщикам, показывает рекомендуемое количество, срочность и объяснение, после чего менеджер проверяет и подтверждает результат.

> Заказы поставщикам автоматически не отправляются. Финальное решение остаётся за ответственным сотрудником.

---

## 1. Цель решения

По официальному ТЗ система должна формировать рекомендуемые заказы по каждому артикулу с учётом:

- истории продаж;
- текущих остатков;
- товаров в пути;
- категорий товаров;
- сезонности;
- устойчивого роста спроса;
- прогноза прироста;
- упущенного спроса в периоды stockout;
- разовых крупных заказов и выбросов;
- поставщика и сроков поставки.

Целевой пользовательский сценарий:

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

В текущем репозитории уже реализованы dashboard, FastAPI backend, расчётное ядро, SQLite-кэш загруженных фрагментов и тесты.

Полный end-to-end сценарий с реальными файлами пока не завершён: backend ожидает от extractor нормализованный fragment вида `supplier + file_type + items`, а текущий `/extract` в .NET extractor пока возвращает preview Excel-таблицы.

| Компонент | Статус | Текущее состояние |
|---|---|---|
| Dashboard | **Implemented** | Загрузка файлов, loading/error states, фильтры, группировка, approve, CSV export |
| Dashboard → `POST /api/upload` | **Implemented** | Один синхронный multipart-запрос |
| FastAPI backend | **Implemented** | `/api/upload`, `/api/recalculate`, `/api/health` |
| SQLite cache | **Implemented** | Последние фрагменты по `(supplier, file_type, sku)` |
| Расчёт по SKU | **Implemented** | Forecast, остаток, in-transit, safety stock, MOQ, urgency |
| Сезонность | **Implemented** | Коэффициенты по календарным месяцам |
| Stockout compensation | **Implemented** | Коррекция месяцев с `stock <= 0` |
| Базовое исключение крупных выбросов | **Implemented** | Robust rule на уровне транзакций |
| Explanation | **Implemented** | Текстовое обоснование по каждой позиции |
| Supplier grouping | **Implemented** | Результат группируется по поставщикам |
| Excel extractor | **In progress** | Excel читается, но canonical fragment ещё не возвращается из `/extract` |
| OpenAI field mapping | **In progress** | Клиент реализован, но основной `/extract` сейчас его не использует |
| End-to-end upload реальных файлов | **In progress** | Нужен финальный контракт extractor → backend |
| Customer-level anomaly | **Not implemented** | Нет отдельного анализа по обезличенному `customer_id` |
| Устойчивый trend / growth | **Not implemented** | Forecast основан на recent baseline + seasonality |
| Отдельный forecast growth input | **Not implemented** | Не передаётся в расчётный контракт |
| Supplier-specific lead time | **Not implemented** | Сейчас используется default `30` дней |
| Интеграция с 1С | **Not implemented** | Прямой интеграции нет |
| Автоотправка поставщику | **Not implemented by design** | Запрещена без подтверждения человека |

---

## 3. Архитектура

Текущий целевой поток:

```text
Dashboard
   │
   │ POST /api/upload
   │ multipart/form-data: files[]
   ▼
FastAPI backend
   │
   ├─ каждый файл → extractor /extract
   ├─ canonical fragments → SQLite cache
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
→ проверка
→ подтверждение
→ CSV export
```

Backend хранит последние загруженные данные по поставщику и типу файла. Поэтому после первой полной загрузки можно обновлять только изменившийся источник, например `transit`, не загружая заново всю историю продаж.

Для расчёта обязательны:

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

Принимает несколько файлов:

```text
multipart/form-data
files: File[]
```

Каждый файл отправляется в extractor отдельным запросом. После получения нормализованных fragments backend обновляет SQLite cache и синхронно пересчитывает затронутых поставщиков.

Ответ:

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

Допустимые значения `urgency`:

- `critical`;
- `soon`;
- `planned`.

### `POST /api/recalculate`

Принимает уже нормализованный Contract 1 и запускает расчёт без загрузки файлов.

### `GET /api/health`

```json
{
  "status": "ok"
}
```

---

## 5. Методология расчёта

Расчёт выполняется отдельно для каждого SKU внутри `process_supplier(...)`.

### 5.1. Исключение крупных разовых транзакций

Текущая реализация использует устойчивый эвристический критерий.

Для истории `qty`:

```text
large transaction:
abs(qty) > median(abs(qty)) × 4
```

Такие операции исключаются из регулярного спроса только если они редкие — не более `15%` истории.

Если крупные продажи повторяются регулярно, алгоритм не удаляет их автоматически, чтобы не уничтожить возможный сезонный паттерн.

> TODO: отдельный customer-level detector для официального требования о крупной покупке одного обезличенного клиента.

### 5.2. Агрегация спроса

После очистки продажи агрегируются по месяцам:

```text
YYYY-MM → sum(qty)
```

### 5.3. Stockout / упущенный спрос

Stockout определяется по остатку:

```text
monthly_stock <= 0
```

Нулевые продажи сами по себе stockout не означают.

Для stockout-месяца спрос оценивается:

1. по среднему спросу того же календарного месяца в другие годы;
2. если такой истории нет — по среднему спросу остальных non-stockout месяцев.

Таким образом периоды отсутствия товара не должны искусственно занижать будущий forecast.

### 5.4. Сезонность

Для каждого календарного месяца рассчитывается:

```text
seasonality_index =
average demand for calendar month
/
average demand for all months
```

Коэффициент `1.25` означает, что спрос этого месяца примерно на 25% выше общего среднего.

### 5.5. Forecast

Текущий forecast использует последние `6` месяцев:

```text
recent mean
→ deseasonalization
→ seasonal coefficient target month
```

Это отражает сезонность и недавний уровень спроса.

> Отдельный устойчивый trend / growth factor пока не реализован.

### 5.6. Категория и safety stock

Категория влияет на страховой запас через волатильность спроса.

```text
CV = std(monthly demand) / mean(monthly demand)
```

Далее:

```text
safety_stock_days = 7 × (1 + average category CV)
```

с ограничением:

```text
5 ≤ safety_stock_days ≤ 21
```

### 5.7. Рекомендуемое количество

В текущей реализации:

```text
lead_time_days = 30
```

Расчёт:

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

Если задан `MOQ`, результат округляется вверх до кратности MOQ.

Если последний остаток отсутствует, текущая реализация принимает его за `0` и добавляет предупреждение в explanation.

### 5.8. Срочность

Срочность определяется по дням покрытия текущим остатком:

```text
< 7 дней   → critical
< 21 дня   → soon
>= 21 дня  → planned
```

---

## 6. Explainability

Для каждой рекомендации формируется объяснение, которое может содержать:

- прогноз спроса;
- сезонный коэффициент;
- размер страхового запаса;
- категорию;
- stockout-коррекцию;
- количество исключённых крупных операций;
- объём исключённых операций;
- дни покрытия текущим остатком;
- предупреждение об отсутствующих данных остатка.

В dashboard объяснение доступно через блок:

```text
Почему такой заказ?
```

---

## 7. Dashboard

Dashboard реализован на:

```text
HTML
CSS
JavaScript ES Modules
```

Без frontend build step и runtime-framework.

Поддерживаются:

- выбор нескольких файлов;
- drag & drop;
- удаление файлов до отправки;
- один синхронный `POST /api/upload`;
- loading state;
- обработка HTTP/network/JSON ошибок;
- partial errors;
- KPI;
- поиск по названию и артикулу;
- фильтр поставщика;
- фильтр срочности;
- группировка по поставщикам;
- раскрываемое explanation;
- подтверждение одной позиции;
- подтверждение всех видимых позиций поставщика;
- фильтр «Только подтверждённые»;
- CSV export.

Подтверждения существуют только на стороне браузера и сбрасываются после нового успешного расчёта или перезагрузки страницы.

---

## 8. Структура проекта

```text
.
├── dashboard/
│   ├── index.html
│   ├── styles.css
│   ├── dashboard.mjs
│   ├── recommendations.mjs
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

### Python backend

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

Установка:

```bash
pip install -r requirements.txt
```

### Dashboard tests

Для frontend-тестов требуется Node.js.

```bash
npm ci --prefix dashboard
```

### Extractor

Extractor — ASP.NET Core проект.

Для запуска требуется подходящий .NET SDK либо Docker.

---

## 10. Запуск

### Backend

Linux / macOS:

```bash
PYTHONPATH=src uvicorn api.main:app --host 127.0.0.1 --port 8001
```

Windows PowerShell:

```powershell
$env:PYTHONPATH="src"
uvicorn api.main:app --host 127.0.0.1 --port 8001
```

Health check:

```text
http://127.0.0.1:8001/api/health
```

### Dashboard

Для просмотра интерфейса:

```bash
python3 -m http.server 8000 --bind 127.0.0.1 --directory dashboard
```

Открыть:

```text
http://127.0.0.1:8000/
```

> Важно: dashboard вызывает относительный `/api/upload`. Обычный `python -m http.server` показывает UI, но не проксирует API. Для полноценного browser end-to-end dashboard и API должны быть доступны через общий origin/reverse proxy либо frontend должен быть настроен на backend URL.

### Docker Compose

```bash
docker compose up --build
```

Определены сервисы:

```text
extractor : 8080
backend   : 8001
dashboard : 8000
```

Текущий dashboard container — обычный static HTTP server и самостоятельно `/api/upload` на backend не проксирует.

---

## 11. Environment variables

`.env.example`:

```env
OPENAI_API_KEY=
```

Реальный ключ должен храниться только локально и не попадать в Git.

Backend также использует:

```text
EXTRACTOR_URL
UPLOADS_DB_PATH
```

В `docker-compose.yml` они задаются для backend автоматически.

`OPENAI_API_KEY` используется OpenAI field-mapping клиентом extractor. Текущий endpoint `/extract` пока не вызывает финальный mapping pipeline.

---

## 12. Тестирование

### Backend / calculation engine

```bash
PYTHONPATH=src pytest -q
```

Проверено на текущем репозитории:

```text
13 passed
```

### Dashboard

```bash
npm test --prefix dashboard
npm run check --prefix dashboard
```

Проверено на текущем репозитории:

```text
19 passed
```

Frontend-тесты покрывают:

- выбор и удаление файлов;
- drag & drop;
- отправку всех файлов одним `POST`;
- блокировку повторной отправки;
- loading state;
- успешное обновление таблицы;
- HTTP/network/JSON errors;
- partial errors;
- фильтры;
- approve;
- explanations;
- CSV export.

Backend-тесты покрывают в том числе:

- сезонность;
- stockout compensation;
- устойчивость к крупной разовой продаже;
- влияние `in_transit`;
- влияние категории на safety stock;
- explanation и urgency;
- API contracts;
- SQLite cache частичных обновлений.

---

## 13. Must Have — текущее покрытие

| Официальное требование | Статус |
|---|---|
| Базовая потребность по SKU | **Partial / mostly implemented** |
| История продаж | **Implemented in calculation contract** |
| Текущие остатки | **Implemented** |
| Товары в пути | **Implemented** |
| Категория товара | **Implemented** |
| Отдельный forecast growth input | **Not implemented** |
| Сезонность | **Implemented** |
| Устойчивый рост спроса | **Not implemented separately** |
| Stockout / lost demand compensation | **Implemented** |
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

1. `extractor /extract` пока не возвращает canonical fragment, который ожидает `/api/upload`, поэтому полный real-file end-to-end ещё не завершён.
2. Customer-level anomaly detection по обезличенному клиенту отсутствует.
3. Отдельная модель устойчивого trend / growth отсутствует.
4. Внешний прогноз прироста не передаётся в расчёт.
5. Lead time сейчас фиксирован значением `30` дней.
6. Нет прямой интеграции с 1С.
7. CSV export выполняется в браузере.
8. Approve — UI-состояние, а не размещение заказа у поставщика.
9. Dashboard и API при отдельном локальном запуске требуют общего origin/proxy для работы относительного `/api/upload`.
10. OpenAI используется только в extractor field-mapping модуле; расчётное ядро является детерминированным и не требует LLM.

---

## 15. Privacy & Security

- клиентские данные должны оставаться обезличенными;
- не использовать неанонимизированные данные клиентов;
- API keys и пароли не должны попадать в Git;
- исходные данные партнёра не должны публиковаться без разрешения;
- `.env` не должен коммититься;
- рекомендации должны проверяться ответственным сотрудником;
- заказ не отправляется поставщику автоматически.

---

## 16. Ближайшие TODO

P0 до полного end-to-end:

- [ ] привести `/extract` к canonical fragment contract;
- [ ] проверить реальные файлы обоих поставщиков через `/api/upload`;
- [ ] обеспечить общий origin/proxy для dashboard → API;
- [ ] выполнить полный smoke test:
  `upload → extract → cache → calculate → JSON → dashboard`.

Следующие улучшения:

- [ ] customer-level anomaly detection;
- [ ] устойчивый trend / growth;
- [ ] отдельный forecast growth input;
- [ ] supplier-specific lead time;
- [ ] при необходимости — XLSX export;
- [ ] заменить оставшиеся demo/mock fixtures только там, где они не нужны тестам.
