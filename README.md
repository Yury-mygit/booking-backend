# booking-backend

Backend для Telegram Mini App бронирования отелей в Кыргызстане
(single-app `book.dev.raftforge.art`).

- **Стек:** FastAPI + SQLAlchemy 2.x async + asyncpg + Alembic
- **Порт:** `127.0.0.1:8026` → `8000` в контейнере
- **БД:** `booking_dev` в `db_shared` (postgres:16)
- **Контейнер:** `booking_dev_app`
- **Бот:** `@rforge_stay_bot` (один на все роли — client/partner/admin)
- **Frontend:** `../frontend-app/` (SPA, vite build → Caddy static)

## Локальный запуск

```bash
cp .env.example .env
# заполнить DATABASE_URL, TG_BOT_TOKEN, TG_WEBHOOK_SECRET
docker compose up -d --build
curl http://127.0.0.1:8026/api/info
```

После правок кода — `docker compose up -d --build` (нет bind-mount исходников).

## Структура

```
app/
├── main.py             — FastAPI app + APIError handler + lifecycle
├── api/
│   ├── router.py       — собирает /api/v1/* роуты
│   ├── info.py         — /api/info (health + db ping)
│   ├── auth.py         — /auth/* (TG initData → session token; whoami; dev-login)
│   ├── public.py       — /public/* (катaлог отелей, без auth)
│   ├── events.py       — SSE: /public/hotels/{slug}/events
│   ├── client.py       — /c/bookings* (бронирование от лица гостя)
│   ├── payments.py     — /c/bookings/{code}/pay/* (mock-провайдер)
│   ├── partner/        — /p/* (отельер), 7 sub-роутеров по доменам:
│   │   ├── __init__.py — собирает router с prefix='/p'
│   │   ├── hotels.py   — CRUD + dashboard + checklist + stats
│   │   ├── rooms.py    — Rooms + Availability + /p/rooms flat
│   │   ├── services.py — hotel services
│   │   ├── bookings.py — incoming + walk-in + confirm/mark-paid/cancel
│   │   ├── clients.py  — clients CRUD + lookup
│   │   ├── staff.py    — members + invites
│   │   └── audit.py    — audit log + CSV
│   ├── admin.py        — /admin/* (верификация партнёров, promote/demote)
│   ├── tg.py           — webhook /tg/bot
│   ├── uploads.py      — фото отелей/комнат/клиентов
│   └── qr.py           — /me/qr (платёжный QR владельца)
├── core/
│   ├── config.py       — pydantic-settings (env)
│   ├── database.py     — async engine + get_db
│   ├── deps.py         — AuthContext + require_role / require_partner_or_staff
│   ├── tg_auth.py      — verify TG initData (HMAC)
│   ├── auth_scope.py   — load_accessible_owners (owner + staff membership)
│   ├── audit.py        — audit(...) helper (write в audit_log)
│   ├── autocancel.py   — фоновая отмена устаревших pending-броней
│   ├── payments.py     — provider interface (MockProvider)
│   ├── pubsub.py       — in-memory pubsub для SSE
│   └── exceptions.py   — APIError
├── services/
│   └── scope.py        — owner-scope DB helpers (get_my_hotel/room/...);
│                         используется api/partner/* и api/uploads.py
├── models/models.py    — SQLAlchemy модели
└── schemas/            — pydantic DTO; view-классы умеют @classmethod from_model
alembic/versions/       — миграции
scripts/
├── seed_demo.py        — демо-данные
├── seed_fictional.py   — больше демо-данных
├── gen_init_data.py    — генерация initData для dev-тестов
└── promote_to_admin.py — выдать роль admin по telegram_id
```

### Правила к файлам кода

- Размер ≤ 300 строк (target), потолок 500.
- Один файл = одна доменная ответственность.
- Верхний docstring 2-5 строк: scope + ключевые инварианты.
- Helpers DB-доступа — в `app/services/scope.py`, не в роут-файле.
- Конверторы model→view — как `@classmethod from_model(cls, ...)` в schemas,
  не отдельные функции в роутах.

## TG webhook

Webhook уже настроен на `https://book.dev.raftforge.art/api/v1/tg/bot`.
Проверка/смена — напрямую через Bot API:

```bash
TOKEN=$(grep TG_BOT_TOKEN= .env | cut -d= -f2)
curl "https://api.telegram.org/bot$TOKEN/getWebhookInfo"
```

## Авторизация

- **TG WebApp:** `initData` от Telegram → backend проверяет HMAC, выдаёт
  session token. Реализация: `app/core/tg_auth.py`, endpoint `/auth/tg`.
- **Single-token model:** сессия не носит роли — права считаются
  per-endpoint по `user.role` (БД-факт) + `accessible_owners`
  (verified partner_profile ИЛИ staff membership). См.
  `app/core/deps.py`: `require_role` / `require_partner_or_staff` /
  `require_admin_access`.
- **Партнёр:** модель **owner + staff** (`partner_staff`) с 4 perm-флагами
  (`manage_hotel/rooms/bookings/staff`). Сотрудник добавляется по `telegram_id`
  напрямую. `accessible_owners` в `/auth/whoami` → селектор владельца в шапке.
- **Audit:** все write-операции партнёра логируются в `audit_log`
  (`GET /p/audit`).

## История

Карты разработки в `~/claude-workspace/history/2026-05-*-booking-*.md`.
Управляющий указатель — `/root/CLAUDE.md` (раздел «booking/»).
