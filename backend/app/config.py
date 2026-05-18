from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str
    pulse_env: str = "dev"
    init_data_max_age: int = 86400
    database_url: str


settings = Settings()  # type: ignore[call-arg]  # values come from env
