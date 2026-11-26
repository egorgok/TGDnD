from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional

class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str

    # Database
    database_url: str
    database_echo: bool = False

    # Redis
    redis_url: str

    # AI Gateways
    ai_text_gateway_url: str
    ai_text_gateway_token: str
    ai_text_model: str = "gpt-6-luna"

    ai_image_gateway_url: str
    ai_image_gateway_token: str
    ai_image_model: str = "gpt-image-2"
    ai_image_quality: str = "low"

    # App settings
    debug: bool = True
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False

@lru_cache()
def get_settings() -> Settings:
    return Settings()
