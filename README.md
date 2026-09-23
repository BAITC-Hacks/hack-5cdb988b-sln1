# HackAlem AI — Track 05: Logistics

## Задание 01 — Автоматический расчёт заказов поставщикам для пополнения склада

**Владелец задачи:** ТОО «Электрокомплект»  
**Основной пользователь:** менеджер отдела закупа.

Цель решения — сформировать рекомендации по пополнению склада по поставщикам с объяснением и срочностью. Финальное решение принимает человек; автоматической отправки заказа поставщику нет.

## Current status

Сейчас в репозитории есть:

- `dashboard/` — готовый UI на HTML/CSS/JS, пока работает на mock-рекомендациях;
- `src/calc_engine/` — расчётное ядро: выбросы, stockout-коррекция, сезонность, учёт остатка и товара в пути, recommended quantity, urgency, explanation;
- `src/api/` — FastAPI с `/api/recalculate`, `/api/upload`, `/api/health`;
- `tests/` — тесты расчётного ядра и API.

`POST /api/upload` уже подготовлен под общий сценарий:

```text
dashboard → POST /api/upload
→ parser.interface.parse_uploaded_files(files)
→ process_supplier(...)
→ JSON → dashboard
```

Но `src/parser/interface.py` в текущем репозитории отсутствует, поэтому `/api/upload` сейчас возвращает `501`. Dashboard также ещё не подключён к API и использует `mock_recommendations.json`.

## Must Have из ТЗ

Требуется:

- учитывать продажи, остатки, товары в пути, категории и прогноз прироста;
- учитывать сезонность и устойчивый рост;
- компенсировать упущенный спрос при stockout;
- исключать разовые крупные заказы, включая аномалии одного клиента;
- выдавать рекомендации по поставщикам с количеством, срочностью и объяснением.

### Implementation status

| Часть | Статус |
|---|---|
| Dashboard | Implemented |
| API | Implemented |
| Расчёт по SKU | Implemented |
| Сезонность | Implemented |
| Stockout compensation | Implemented |
| Учёт in-transit | Implemented |
| Базовое исключение крупных выбросов | Implemented |
| Explanation / urgency | Implemented |
| Парсер загружаемых файлов | Not implemented |
| Dashboard → API integration | Not implemented |
| Customer-level anomaly | Not implemented |
| Отдельный forecast growth input | Not implemented |

## Методология расчёта

Текущий pipeline для SKU:

1. крупные редкие транзакции исключаются из регулярного спроса;
2. продажи агрегируются по месяцам;
3. месяцы с `stock <= 0` корректируются на оценку упущенного спроса;
4. рассчитывается сезонный коэффициент;
5. прогнозируется спрос на следующий месяц;
6. потребность считается с учётом срока поставки, страхового запаса, текущего остатка и товара в пути;
7. формируются `recommended_qty`, `urgency` и текстовое объяснение.

> TODO: customer-level anomaly detection и отдельный учёт переданного forecast growth.

## Структура

```text
.
├── dashboard/
├── src/
│   ├── api/
│   └── calc_engine/
├── tests/
├── Docs/
├── requirements.txt
└── README.md
```

## Запуск dashboard

Из корня репозитория:

```bash
python3 -m http.server 8000 --bind 127.0.0.1 --directory dashboard
```

Открыть:

```text
http://localhost:8000/
```

## Запуск API

```bash
pip install -r requirements.txt
uvicorn api.main:app --app-dir src --reload
```

## Тесты

```bash
pytest -q
node --test dashboard/tests/*.test.mjs
```

## Ограничения

- dashboard пока использует mock data;
- parser файлов ещё не подключён;
- end-to-end `upload → parse → calculate → render` ещё не завершён;
- customer-level anomaly и отдельный growth forecast ещё не реализованы;
- заказы поставщикам автоматически не отправляются.
