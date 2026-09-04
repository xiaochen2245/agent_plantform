"""测试环境：内存 SQLite（StaticPool 共享连接），ASGITransport 直挂 app。"""
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["JWT_SECRET"] = "test-only-jwt-secret-32-bytes-minimum-padding!"
os.environ["ENCRYPTION_KEY"] = "test-only-encryption-key"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:5173,http://localhost:8000"
os.environ["DEBUG"] = "true"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
# 契约 v4：上传卷指向临时目录，避免测试污染真实 backend/uploads
import tempfile as _tempfile

os.environ["UPLOAD_DIR"] = _tempfile.mkdtemp(prefix="ap-uploads-")

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings

get_settings.cache_clear()

from app.db.init import init_db  # noqa: E402  (env 先于 import)
from app.main import app  # noqa: E402

# 测试隔离：剥离开发者本机 backend/.env 注入的 Dify 应用 key（config 导入时
# _load_dynamic_env_keys 会写 os.environ），保证测试里 DifyClient 走缺省 demo-key，
# 不受任何本地环境影响。
for _k in list(os.environ):
    if _k.startswith("DIFY_API_KEY_APP_"):
        del os.environ[_k]


@pytest.fixture(autouse=True)
async def fresh_db():
    """每个测试前重建表+种子（含 3 个 Agent），隔离状态。"""
    await init_db(drop=True)
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def client() -> AsyncClient:
    # ASGITransport 不跑 lifespan，fresh_db 已覆盖初始化；dify 由测试 override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def login(c: AsyncClient, email: str = "admin@company.com", password: str = "admin123") -> None:
    resp = await c.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
