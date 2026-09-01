from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


def test_hash_and_verify_password():
    h = hash_password("hello123")
    assert h != "hello123"
    assert verify_password("hello123", h) is True
    assert verify_password("wrong", h) is False


def test_jwt_roundtrip():
    token = create_access_token(user_id=42, roles=["USER"], dept_id=7)
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "42"
    assert payload["roles"] == ["USER"]
    assert payload["dept_id"] == 7
    assert "jti" in payload and "exp" in payload


def test_jwt_garbage_returns_none():
    assert decode_access_token("not-a-jwt") is None


def test_refresh_token_hash_deterministic():
    raw, hashed = create_refresh_token()
    assert raw != hashed
    assert hash_refresh_token(raw) == hashed
