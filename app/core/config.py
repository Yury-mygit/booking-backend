from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "booking-dev"
    version: str = "0.1.0"
    log_level: str = "info"

    database_url: str

    tg_bot_token_client: str = ""
    tg_bot_token_partner: str = ""
    tg_bot_token_admin: str = ""

    tg_init_data_max_age_sec: int = 3600
    session_ttl_sec: int = 60 * 60 * 24 * 30

    tg_webhook_secret: str = ""
    public_base_client: str = "https://book.dev.raftforge.art/"
    public_base_partner: str = "https://book-partner.dev.raftforge.art/"
    public_base_admin: str = "https://book-admin.dev.raftforge.art/"

    dev_mode: bool = False

    storage_path: str = "/app/storage"
    photo_max_bytes: int = 5 * 1024 * 1024


settings = Settings()
