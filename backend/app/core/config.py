"""应用配置（环境变量 / .env，dev 默认值可直接跑）。"""
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_dynamic_env_keys() -> None:
    """pydantic-settings 只把 .env 中「已声明字段」载入 Settings；
    DIFY_API_KEY_APP_<id> 是动态键（app/dify/client.py 经 os.environ 读取），
    这里手动注入，保证 .env 与真实环境变量行为一致。"""
    env_file = Path(__file__).resolve().parents[2] / ".env"  # backend/.env
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DIFY_API_KEY_APP_") and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


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
    # HTTP 部署阶段（TLS 未就绪）可显式设 COOKIE_SECURE=false；缺省跟随 DEBUG
    COOKIE_SECURE: bool | None = None

    # 演示应用种子（apps 表的契约演示行）：缺省跟随 DEBUG；生产可显式 SEED_DEMO_APPS=true 强制
    SEED_DEMO_APPS: bool | None = None

    @property
    def seed_demo_apps(self) -> bool:
        return self.SEED_DEMO_APPS if self.SEED_DEMO_APPS is not None else self.DEBUG

    @property
    def cookie_secure(self) -> bool:
        return self.COOKIE_SECURE if self.COOKIE_SECURE is not None else not self.DEBUG

    JWT_SECRET: str = "dev-only-jwt-secret-change-me-in-production-32b"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # 预留：Dify API Key 加密密钥（本切片未使用，避免后续配置面变化）
    ENCRYPTION_KEY: str = "dev-only-encryption-key-not-used-yet"

    # dev 默认 SQLite（生产由 compose 覆盖为 postgresql+asyncpg://...）
    DATABASE_URL: str = "sqlite+aiosqlite:///./dev.db"

    ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000"

    # 契约 v4：上传本地卷（相对 backend cwd；测试指向 tmp 目录）
    UPLOAD_DIR: str = "uploads"

    # B5：过期附件清理 TTL（天）；启动时后台扫一遍 UPLOAD_DIR
    UPLOAD_TTL_DAYS: int = 30

    # Dify 服务 API 基址（本切片不真调，测试用 FakeDify；真实联调时指向 .226）
    DIFY_BASE_URL: str = "http://192.168.20.226"

    SEED_ADMIN_EMAIL: str = "admin@company.com"
    SEED_ADMIN_PASSWORD: str = "admin123"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


_load_dynamic_env_keys()
