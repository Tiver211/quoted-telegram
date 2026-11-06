from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    bot_token: str
    proxy_url: SecretStr | None = None
    database_url: str = "sqlite+aiosqlite:////data/quoted.db"
    staging_chat_id: int
    data_dir: Path = Path("/data")
    render_timeout: int = Field(default=30, ge=1, le=120)
    max_media_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    log_level: str = "INFO"

    @field_validator("proxy_url", mode="before")
    @classmethod
    def validate_proxy_url(cls, value):
        if value is None or value == "":
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        try:
            parsed = urlsplit(raw)
            valid = (
                parsed.scheme in {"http", "socks4", "socks5"} and parsed.hostname and parsed.port
            )
            if not valid or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError(
                "PROXY_URL must be an http://, socks4:// or socks5:// URL with host and port"
            ) from None
        return value
