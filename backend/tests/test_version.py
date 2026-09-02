"""版本端点：GET /api/version 无需登录，供前端僵尸标签页检测比对。"""

from httpx import AsyncClient


async def test_version_endpoint_shape_no_auth(client: AsyncClient):
    """无 cookie 可访问；返回非空字符串 version（注入值/git sha/'dev' 均合法）。"""
    resp = await client.get("/api/version")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("version"), str)
    assert body["version"] != ""


async def test_version_stable_across_calls(client: AsyncClient):
    """版本在进程内解析一次并保持稳定（lru_cache 语义）。"""
    a = (await client.get("/api/version")).json()["version"]
    b = (await client.get("/api/version")).json()["version"]
    assert a == b
