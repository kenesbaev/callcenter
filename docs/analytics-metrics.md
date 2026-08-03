# K-Line analytics: единые правила метрик

`services/api/teamora_api/analytics_service.py` — единственный query-layer для `/app/overview`,
`/app/analytics` и всех API `/api/v1/analytics/*`.

## Период и timezone

- `date_from` включается, `date_to` исключается.
- Дата без UTC offset интерпретируется в переданном IANA `timezone`.
- Если timezone не передан, используется timezone проекта, затем компании.
- SQL-фильтры используют UTC. «Сегодня» — `[00:00, 00:00 следующего дня)` в выбранном timezone.
- Максимальный период — 366 дней.
- До 48 часов график группируется по часу, более длинный период — по дню.
- Предыдущий период имеет ту же продолжительность и заканчивается в `date_from`.

## Словарь метрик

| Метрика                 | Timestamp отбора                           | Формула                                                                    |
| ----------------------- | ------------------------------------------ | -------------------------------------------------------------------------- |
| Начатые попытки         | `Call.started_at`                          | `count(distinct Call.id)`; queued без `started_at` исключён                |
| Соединённые             | `Call.started_at`                          | начатые звонки, у которых есть `answered_at`                               |
| Процент дозвона         | `Call.started_at`                          | `connected / attempted × 100`; при нулевом denominator значение недоступно |
| Успешные                | `Call.started_at`                          | звонки с историческим `CallOutcome.category = successful`                  |
| Процент успеха          | `Call.started_at`                          | `successful / calls_with_outcome × 100`                                    |
| Успех среди соединённых | `Call.started_at`                          | `successful / connected × 100`; возвращается отдельным полем               |
| Средняя длительность    | `Call.started_at`                          | среднее `ended_at - answered_at` только для неотрицательных интервалов     |
| Активные звонки         | текущий snapshot                           | initiated, ringing, active, on_hold, transfer_requested, transferring      |
| AI / оператор           | `Call.started_at`                          | канонический `caller_type`, не transcript                                  |
| Simulator / SIP         | `Call.started_at`                          | канонический `channel`; simulator и `is_demo` дают data-quality flag       |
| Запрос перевода         | `CallEvent.occurred_at`                    | distinct `call_id` с `transfer.requested`                                  |
| Успешный перевод        | `CallEvent.occurred_at`                    | distinct `call_id` с `transfer.completed`                                  |
| Неуспешный перевод      | `TransferRequest.resolved_at/requested_at` | distinct `call_id` со status `failed`                                      |
| AI-минуты               | `UsageRecord.occurred_at`                  | сумма сохранённых AI minute metrics                                        |
| AI-стоимость            | `UsageRecord.occurred_at`                  | сумма сохранённых pricing snapshots; если у usage нет цены — `unavailable` |
| Callback                | `CallbackTask.created_at`                  | задачи типа callback, созданные в периоде                                  |

Процентное сравнение не возвращается, если предыдущий показатель равен нулю. Стоимость
SIP, DID и Asterisk не входит в AI cost.

## Data quality

- `test_data_present` — в выборке есть demo/simulator;
- `missing_outcome` — не у всех начатых звонков сохранён результат;
- `missing_usage_price` — AI usage есть, pricing snapshot отсутствует;
- `partial_ai_usage` — оценена только часть usage;
- `invalid_duration_excluded` — отрицательная длительность исключена;
- `incomplete_historical_metadata` — у истории отсутствует язык или provider.

Телефоны в recent calls маскируются на backend. Operator получает только собственную аналитику
и назначенные проекты; AI cost скрыт. Owner, manager и analyst получают tenant-level read scope с
проверкой доступного проекта. Все исходные таблицы защищены существующими PostgreSQL RLS policies.
