"""Configuration Management"""
import os
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import field_validator


def _resolve_env(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return value
    return default


def _coerce_debug(value) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "debug", "dev", "development"}:
        return True
    if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
        return False
    return True

class Settings(BaseSettings):
    # PostgreSQL
    DATABASE_URL: str = _resolve_env(
        "DATABASE_URL",
        default="postgresql://ecommerce:ecommerce@localhost:5432/ecommerce_db"
    )
    DATABASE_NAME: str = _resolve_env("DATABASE_NAME", default="ecommerce_db")
    
    # JWT
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Rate Limiting
    REQUESTS_PER_MINUTE: int = 60
    PURCHASE_LIMIT_PER_MINUTE: int = 5  # ป้องกัน double purchase
    
    # Server
    DEBUG: bool = os.getenv("DEBUG", True)
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug(cls, value):
        return _coerce_debug(value)
    
    class Config:
        env_file = ".env"

settings = Settings()
