"""应用配置（环境变量 / .env，dev 默认值可直接跑）。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "Agent Platform"
    # dev 默认开启：cookie secure=False、CORS 放开、CSRF 白名单自动含 5173。生产必须显式覆盖。
    DEBUG: bool = True

    JWT_SECRET: str = "dev-only-jwt-secret-change-me-in-production-32b"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # 预留：Dify API Key 加密密钥（本切片未使用，避免后续配置面变化）
    ENCRYPTION_KEY: str = "dev-only-encryption-key-not-used-yet"

    # dev 默认 SQLite（生产由 compose 覆盖为 postgresql+asyncpg://...）
    DATABASE_URL: str = "sqlite+aiosqlite:///./dev.db"

    ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000"

    SEED_ADMIN_EMAIL: str = "admin@company.com"
    SEED_ADMIN_PASSWORD: str = "admin123"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
