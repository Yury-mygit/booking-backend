from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "booking-dev"
    version: str = "0.1.0"
    log_level: str = "info"

    database_url: str

    tg_bot_username: str = "rforge_stay_bot"
    tg_bot_token: str = ""

    tg_init_data_max_age_sec: int = 3600
    session_ttl_sec: int = 60 * 60 * 24 * 30

    tg_webhook_secret: str = ""

    # Этап 4 client-hotel-chat: TG-уведомления о новых чат-сообщениях.
    # На dev выставляем false через `CHAT_TG_NOTIFICATIONS_ENABLED=false`,
    # чтобы не спамить себе личку при тестировании.
    chat_tg_notifications_enabled: bool = True
    # URL для inline-кнопки «Начать» в TG-боте и deep-link'ов.
    # Переопределяется через env PUBLIC_BASE_APP.
    public_base_app: str = "https://book.dev.raftforge.art/"

    dev_mode: bool = False

    # storage_path сейчас используется только qr.py (QR-коды клиентов).
    # Photo-storage съехал в media-сервис (Stage 2 + 5 booking → media,
    # 2026-06-07). После миграции QR в media volume можно удалить.
    storage_path: str = "/app/storage"
    photo_max_bytes: int = 5 * 1024 * 1024

    # media-сервис (карта 2026-05-27-booking-media-migration.md, Stage 1).
    # Внутренний URL — server-to-server в docker `shared` сети без Caddy.
    # Публичный — для построения URL'ов в ответах API (фронт получает абсолют).
    media_internal_url: str = "http://media_dev_app:8000"
    media_public_base: str = "https://media.dev.raftforge.art"
    # Токен для эндпоинта /api/v1/media-refs (GC консьюмер). Шлёт media.
    media_gc_token: str = ""


settings = Settings()
