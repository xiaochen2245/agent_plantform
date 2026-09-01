"""鉴权闭环（契约路径）。种子用户由 init_db 提供。"""
from app.core.config import settings

ADMIN = {"email": settings.SEED_ADMIN_EMAIL, "password": settings.SEED_ADMIN_PASSWORD}


async def test_login_success_sets_both_cookies(client):
    resp = await client.post("/api/auth/login", json=ADMIN)
    assert resp.status_code == 200
    assert resp.text == ""  # 契约：空体，token 只走 cookie
    set_cookies = "; ".join(resp.headers.get_list("set-cookie")).lower()
    assert "access_token_cookie=" in set_cookies
    assert "refresh_token_cookie=" in set_cookies
    assert "httponly" in set_cookies
    assert "samesite=strict" in set_cookies
    assert resp.cookies.get("access_token_cookie")
    assert resp.cookies.get("refresh_token_cookie")


async def test_login_wrong_password_401(client):
    resp = await client.post(
        "/api/auth/login",
        json={"email": ADMIN["email"], "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


async def test_me_requires_auth(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_me_after_login(client):
    await client.post("/api/auth/login", json=ADMIN)
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == ADMIN["email"]
    assert "PLATFORM_ADMIN" in body["roles"]
    assert body["dept_id"] is None


async def test_refresh_rotation_invalidates_old(client):
    login = await client.post("/api/auth/login", json=ADMIN)
    assert login.status_code == 200
    old_refresh = client.cookies.get("refresh_token_cookie")

    first = await client.post("/api/auth/refresh")
    assert first.status_code == 200
    assert client.cookies.get("refresh_token_cookie") != old_refresh

    # 旧 refresh 重放 → 401
    client.cookies.set("refresh_token_cookie", old_refresh)
    replay = await client.post("/api/auth/refresh")
    assert replay.status_code == 401


async def test_logout_clears_cookies(client):
    await client.post("/api/auth/login", json=ADMIN)
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 200
    me = await client.get("/api/auth/me")
    assert me.status_code == 401


async def test_csrf_blocks_evil_origin(client):
    resp = await client.post(
        "/api/auth/login",
        json=ADMIN,
        headers={"Origin": "https://evil.com"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden: invalid origin"


async def test_csrf_allows_whitelisted_origin(client):
    resp = await client.post(
        "/api/auth/login",
        json=ADMIN,
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 200


async def test_expired_access_token_401(client):
    import jose.jwt as jwt
    from datetime import datetime, timedelta, timezone

    from app.core.config import settings as s

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = jwt.encode(
        {
            "sub": "1",
            "roles": ["USER"],
            "dept_id": None,
            "jti": "expired",
            "iat": int((past - timedelta(minutes=15)).timestamp()),
            "exp": int(past.timestamp()),
        },
        s.JWT_SECRET,
        algorithm=s.JWT_ALGORITHM,
    )
    client.cookies.set("access_token_cookie", expired)
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
