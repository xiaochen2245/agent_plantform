"""GET /api/apps/me：鉴权 + 契约形状（3 个种子 Agent）。"""
from httpx import AsyncClient

from tests.conftest import login


async def test_apps_me_requires_auth(client: AsyncClient):
    resp = await client.get("/api/apps/me")
    assert resp.status_code == 401


async def test_apps_me_shape_matches_contract(client: AsyncClient):
    await login(client)
    resp = await client.get("/api/apps/me")
    assert resp.status_code == 200
    apps = resp.json()["apps"]
    assert [a["id"] for a in apps] == [1, 2, 3]
    assert apps[0] == {
        "id": 1,
        "name": "IT 运维助手",
        "description": "解答服务器、网络与账号问题",
        "mode": "chat",
    }
    assert apps[2]["mode"] == "agent"
