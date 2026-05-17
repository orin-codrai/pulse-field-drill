from pydantic import BaseModel, ConfigDict


class TelegramUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None
    is_premium: bool | None = None
    allows_write_to_pm: bool | None = None
    added_to_attachment_menu: bool | None = None
    photo_url: str | None = None
