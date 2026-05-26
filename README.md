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
│   ├── auth.py         — TG initData → session token
│   ├── public.py       — /public/hotels, /public/rooms (без auth)
│   ├── events.py       — SSE: /public/hotels/{slug}/events
│   ├── client.py       — /c/* (бронирование от лица гостя)
│   ├── payments.py     — /c/bookings/{code}/pay/* (mock-провайдер)
│   ├── partner.py      — /p/* (отельер: hotels/rooms/bookings/staff/audit)
│   ├── admin.py        — /admin/* (верификация партнёров, promote/demote)
│   ├── tg.py           — webhook /tg/bot + bot-команды
│   ├── uploads.py      — фото отелей/комнат/клиентов
│   └── qr.py           — /me/qr (платёжный QR владельца)
├── core/
│   ├── config.py       — pydantic-settings (env)
│   ├── database.py     — async engine + get_db
│   ├── tg_auth.py      — verify TG initData (HMAC)
│   └── exceptions.py   — APIError
├── models/models.py    — SQLAlchemy модели
└── schemas/            — pydantic DTO (auth/hotels/bookings/partner/admin)
alembic/versions/       — миграции (~16 ревизий)
scripts/
├── seed_demo.py        — демо-данные
├── seed_fictional.py   — больше демо-данных
├── gen_init_data.py    — генерация initData для dev-тестов
└── promote_to_admin.py — выдать роль admin по telegram_id
```

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
