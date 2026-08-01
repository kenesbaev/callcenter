# Команда, роли и присутствие операторов

Этап 10 расширяет существующие `users`, `memberships`, `project_users`, `human_operators`,
`operator_statuses` и `invitations`. Параллельной системы пользователей или авторизации нет.

## Граница данных

- `users` хранит глобальную учётную запись и нормализованный e-mail.
- `memberships` хранит роль, tenant-профиль, блокировку и optimistic `state_version`.
- `project_users` хранит назначения сотрудника на проекты.
- `human_operators` хранит внутренний номер и доступность для перевода.
- `operator_statuses` хранит только ручной статус.
- `operator_presences` хранит heartbeat отдельных вкладок браузера.
- `invitations` хранит только SHA-256 hash токена; полный токен возвращается один раз в
  development/test.
- `invitation_projects` фиксирует tenant-safe назначения приглашения.
- `team_command_submissions` защищает повторяемые административные команды.

Новые tenant-таблицы используют `tenant_id`, составные внешние ключи, `ENABLE ROW LEVEL
SECURITY`, `FORCE ROW LEVEL SECURITY` и tenant policy через `app.tenant_id`.

## Роли

| Действие                             | Owner              | Manager         | Operator            | Analyst       |
| ------------------------------------ | ------------------ | --------------- | ------------------- | ------------- |
| Просмотр всей команды                | да                 | да              | только свой профиль | да, read-only |
| Приглашение manager/operator/analyst | да                 | да              | нет                 | нет           |
| Изменение owner                      | передача ownership | нет             | нет                 | нет           |
| Изменение operator/analyst           | да                 | да              | нет                 | нет           |
| Блокировка/восстановление            | да                 | кроме owner     | нет                 | нет           |
| Назначение проектов                  | да                 | да, кроме owner | нет                 | нет           |
| Собственный ручной статус            | да                 | да              | да                  | нет           |
| Dialer                               | да                 | да              | да                  | нет           |

Последнего активного owner нельзя заблокировать или понизить. Самоблокировка запрещена.
Передача ownership блокирует membership-строки и атомарно повышает нового владельца и понижает
текущего до manager. Все изменения требуют `expected_version` и пишутся в `audit_logs`.

## Присутствие

Ручные статусы: `available`, `away`, `on_break`, `offline`. Клиент не может установить `busy`
или `on_hold`.

Фактический статус рассчитывается в таком порядке:

1. активный звонок `on_hold` → `on_hold`;
2. любой другой незавершённый звонок → `busy`;
3. заблокированная membership, ручной `offline` или отсутствие свежего heartbeat → `offline`;
4. иначе используется ручной статус.

Heartbeat TTL — 90 секунд, UI отправляет heartbeat каждые 30 секунд. Каждая вкладка имеет свой
`session_key`; оператор остаётся online, пока свежа хотя бы одна вкладка. Logout закрывает все
presence-сессии membership. Reload восстанавливает presence с сохранённым tab key. После
терминального состояния звонка фактический статус снова равен ручному.

Новый Dialer assignment выдаётся только фактически `available` сотруднику. Recovery уже
назначенного клиента выполняется раньше этой проверки, поэтому активная работа не теряется.

## Приглашения

Lifecycle: `pending → accepted`, `pending → cancelled`, `pending → expired`; cancelled/expired
приглашение можно перевыпустить с новым токеном. В tenant одновременно допускается только одно
pending-приглашение на e-mail. Повторное принятие уже принятого токена возвращает тот же
membership без дублей.

Для существующего глобального пользователя требуется его текущий пароль. Для нового создаётся
обычный `User`, затем tenant `Membership` и `ProjectUser`. Реальная отправка e-mail намеренно не
реализована: это ограничение текущего этапа.

## API

- `GET /api/v1/team`, `GET /api/v1/team/{id}`, `GET /api/v1/team/me`
- `PATCH /api/v1/team/{id}/profile`
- `PUT /api/v1/team/{id}/role`, `PUT /api/v1/team/{id}/projects`
- `POST /api/v1/team/{id}/block`, `/restore`, `/transfer-ownership`
- `GET/POST /api/v1/team/invitations`
- `POST /api/v1/team/invitations/{id}/reissue`, `/cancel`
- `POST /api/v1/team/invitations/accept`
- `GET /api/v1/team/presence`, `POST /presence/heartbeat`, `PUT /presence/status`
- `GET /api/v1/team/transfer-candidates?project_id=...`
- `GET /api/v1/team/{id}/history`

До Этапа 12 статусы обновляются polling. Реальная отправка e-mail, WebSocket, SIP/Asterisk transfer
и production deployment не входят в Этап 10.
