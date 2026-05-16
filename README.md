# booking-backend

Backend для трёх Telegram WebApp бронирования отелей в Кыргызстане.

- **Стек:** FastAPI + SQLAlchemy 2.x async + asyncpg + Alembic
- **Порт:** `127.0.0.1:8026` (хост) → `8000` (контейнер)
- **БД:** `booking_dev` в `db_shared`
- **Контейнер:** `booking_dev_app`

## Локальный запуск

```bash
cp .env.example .env
# заполнить DATABASE_URL и TG_BOT_TOKEN_*
docker compose up -d --build
curl http://127.0.0.1:8026/api/info
```

## Структура

```
app/
├── main.py             — FastAPI app + APIError handler
├── api/
│   ├── info.py         — /api/info (db ping)
│   └── router.py       — /api/v1/* (пока пустой)
├── core/
│   ├── config.py       — pydantic-settings (env)
│   ├── database.py     — async engine + get_db
│   └── exceptions.py   — APIError
└── models/
    └── models.py       — SQLAlchemy Base (модели — следующий этап)
alembic/                — migrations (baseline без revisions)
```

## Этапы — см. `~/claude-workspace/history/2026-05-16-booking-skeleton.md`.
