"""RAGFlow 租户开通器：全自动影子账号链路（零 UI）。

链路（全部 spike 实测）：
  1. POST /api/v1/users            注册（密码 RSA(base64(pwd))，公钥随镜像分发）
  2. POST /api/v1/auth/login       登录（cookie 会话）
  3. POST /api/v1/system/tokens    生成 API key（网关持有）
  4. PUT  /api/v1/providers        注册 SILICONFLOW
  5. POST /api/v1/providers/SILICONFLOW/instances   注入模型 key
  6. PATCH /api/v1/models/default  绑默认 embedding + rerank（必须在建库前！）
  7. POST /api/v1/datasets         建默认库

transport 可注入（测试）；RSA 用 cryptography（python-jose[cryptography] 已带）。
"""
import base64
import secrets

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.config import settings


class ProvisionError(Exception):
    def __init__(self, step: str, message: str) -> None:
        super().__init__(f"[{step}] {message}")
        self.step = step


def _public_pem() -> str:
    if settings.RAGFLOW_PUBLIC_KEY:
        return settings.RAGFLOW_PUBLIC_KEY
    if settings.RAGFLOW_PUBLIC_KEY_B64:
        import base64 as _b64
        return _b64.b64decode(settings.RAGFLOW_PUBLIC_KEY_B64).decode()
    raise ProvisionError("config", "RAGFLOW_PUBLIC_KEY(_B64) not configured")


def ragflow_encrypt_password(plain: str) -> str:
    """RAGFlow 前端同款：RSA(PKCS1v15, base64(pwd)) 再 base64（见镜像 api/utils/crypt.py）。"""
    pub = serialization.load_pem_public_key(_public_pem().encode())
    b64_pwd = base64.b64encode(plain.encode())
    cipher = pub.encrypt(b64_pwd, padding.PKCS1v15())  # type: ignore[union-attr]
    return base64.b64encode(cipher).decode()


class RagflowProvisioner:
    """一次性开通客户端：完成链路后即弃。"""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.RAGFLOW_BASE_URL.rstrip("/"),
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, step: str, method: str, path: str, **kw: object) -> dict:
        resp = await self._client.request(method, path, **kw)  # type: ignore[arg-type]
        try:
            body = resp.json()
        except ValueError:
            body = {"message": resp.text[:200]}
        if resp.status_code >= 400 or body.get("code") not in (0, None):
            raise ProvisionError(step, str(body.get("message") or resp.status_code))
        return body

    async def provision(self, email: str) -> tuple[str, str, str]:
        """返回 (api_token, 默认dataset_id, dataset_name)。密码内部生成、调用方保存。"""
        password = secrets.token_urlsafe(18)
        enc_pw = ragflow_encrypt_password(password)

        await self._call("register", "POST", "/api/v1/users", json={
            "nickname": email.split("@")[0], "email": email, "password": enc_pw,
        })
        await self._call("login", "POST", "/api/v1/auth/login", json={
            "email": email, "password": enc_pw,
        })  # 会话 cookie 自动落在 self._client
        tok = await self._call("token", "POST", "/api/v1/system/tokens")
        data = tok.get("data")
        api_token = data.get("token") if isinstance(data, dict) else str(data or "")
        if not api_token:
            raise ProvisionError("token", f"unexpected token payload: {tok}")

        sf_key = settings.SILICONFLOW_API_KEY
        if sf_key:
            await self._call("provider", "PUT", "/api/v1/providers",
                             json={"provider_name": "SILICONFLOW", "api_key": sf_key})
            await self._call("instance", "POST",
                             "/api/v1/providers/SILICONFLOW/instances",
                             json={"instance_name": "sf-main", "api_key": sf_key})
            await self._call("default-embedding", "PATCH", "/api/v1/models/default", json={
                "model_type": "embedding", "model_provider": "SILICONFLOW",
                "model_instance": "sf-main", "model_name": settings.RAGFLOW_EMBEDDING_MODEL,
            })
            await self._call("default-rerank", "PATCH", "/api/v1/models/default", json={
                "model_type": "rerank", "model_provider": "SILICONFLOW",
                "model_instance": "sf-main", "model_name": settings.RAGFLOW_RERANK_MODEL,
            })

        ds = await self._call("dataset", "POST", "/api/v1/datasets",
                              json={"name": "default"}, headers={
                                  "Authorization": f"Bearer {api_token}"})
        ds_data = ds.get("data") or {}
        dataset_id = ds_data.get("id") or ""
        if not dataset_id:
            raise ProvisionError("dataset", f"no dataset id: {ds}")
        return api_token, dataset_id, password
