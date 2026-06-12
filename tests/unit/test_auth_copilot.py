"""Unit tests for flowforge.auth.copilot device-flow + session swap."""

from __future__ import annotations

import io
import json
import os
import urllib.error
from typing import Any
from unittest.mock import patch

import pytest

from flowforge.auth import copilot as auth_mod


@pytest.fixture(autouse=True)
def _isolate_oauth_path(tmp_path, monkeypatch):
    """Redirect the OAuth-token cache to a temp dir for every test."""
    monkeypatch.setattr(auth_mod, "OAUTH_TOKEN_PATH", tmp_path / "copilot-oauth.json")
    auth_mod.clear_session_cache()
    yield
    auth_mod.clear_session_cache()


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="x", code=code, msg="err", hdrs=None, fp=io.BytesIO(b"")
    )


def test_load_oauth_token_returns_none_when_missing():
    assert auth_mod.load_oauth_token() is None


def test_save_and_load_oauth_token_roundtrip(tmp_path):
    auth_mod._save_oauth_token("ghu_test123")
    assert auth_mod.load_oauth_token() == "ghu_test123"
    # File mode is 0o600
    mode = os.stat(auth_mod.OAUTH_TOKEN_PATH).st_mode & 0o777
    assert mode == 0o600


def test_load_oauth_token_handles_corrupt_file():
    auth_mod.OAUTH_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    auth_mod.OAUTH_TOKEN_PATH.write_text("{not json")
    assert auth_mod.load_oauth_token() is None


def test_device_login_happy_path(monkeypatch):
    """Init returns code+url; first poll says pending; second returns token."""
    poll_responses: list[dict[str, Any]] = [
        {"error": "authorization_pending"},
        {"access_token": "ghu_devicetoken"},
    ]
    init_response = {
        "device_code": "DC",
        "user_code": "ABCD-1234",
        "verification_uri": "https://github.com/login/device",
        "interval": 5,
        "expires_in": 300,
    }

    def fake_post(url: str, data: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
        if url == auth_mod.DEVICE_CODE_URL:
            assert data["client_id"] == auth_mod.COPILOT_CLIENT_ID
            assert data["scope"] == "read:user"
            return init_response
        assert url == auth_mod.ACCESS_TOKEN_URL
        return poll_responses.pop(0)

    monkeypatch.setattr(auth_mod, "_http_post_form", fake_post)

    out = io.StringIO()
    token = auth_mod.device_login(output=out, poll_interval_override=0.0)

    assert token == "ghu_devicetoken"
    assert auth_mod.load_oauth_token() == "ghu_devicetoken"
    assert "ABCD-1234" in out.getvalue()


def test_device_login_handles_slow_down(monkeypatch):
    poll_responses: list[dict[str, Any]] = [
        {"error": "slow_down"},
        {"access_token": "ghu_x"},
    ]

    def fake_post(url: str, data: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
        if url == auth_mod.DEVICE_CODE_URL:
            return {"device_code": "DC", "user_code": "X", "interval": 1, "expires_in": 60}
        return poll_responses.pop(0)

    monkeypatch.setattr(auth_mod, "_http_post_form", fake_post)
    token = auth_mod.device_login(output=io.StringIO(), poll_interval_override=0.0)
    assert token == "ghu_x"


def test_device_login_raises_on_access_denied(monkeypatch):
    def fake_post(url: str, data: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
        if url == auth_mod.DEVICE_CODE_URL:
            return {"device_code": "DC", "user_code": "X", "interval": 1, "expires_in": 60}
        return {"error": "access_denied"}

    monkeypatch.setattr(auth_mod, "_http_post_form", fake_post)
    with pytest.raises(auth_mod.CopilotAuthError, match="access_denied"):
        auth_mod.device_login(output=io.StringIO(), poll_interval_override=0.0)


def test_ensure_oauth_token_returns_cached(monkeypatch):
    auth_mod._save_oauth_token("ghu_cached")
    # Should not call device_login.
    monkeypatch.setattr(
        auth_mod,
        "device_login",
        lambda **_: pytest.fail("device_login must not be called"),
    )
    assert auth_mod.ensure_oauth_token() == "ghu_cached"


def test_ensure_oauth_token_non_interactive_raises_when_missing():
    with pytest.raises(auth_mod.CopilotAuthError, match="copilot-login"):
        auth_mod.ensure_oauth_token(interactive=False)


def test_get_session_token_swaps_and_caches(monkeypatch):
    calls = {"n": 0}

    def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        calls["n"] += 1
        assert url == auth_mod.SESSION_TOKEN_URL
        assert headers["Authorization"] == "token ghu_a"
        return {"token": "tid_session", "expires_at": 1_000_000.0}

    monkeypatch.setattr(auth_mod, "_http_get_json", fake_get)

    tok1 = auth_mod.get_session_token("ghu_a", now=1.0)
    tok2 = auth_mod.get_session_token("ghu_a", now=1.0)
    assert tok1 == tok2 == "tid_session"
    assert calls["n"] == 1  # second call hit the cache


def test_get_session_token_refreshes_near_expiry(monkeypatch):
    payloads = [
        {"token": "tid_one", "expires_at": 100.0},
        {"token": "tid_two", "expires_at": 9_999.0},
    ]

    def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        return payloads.pop(0)

    monkeypatch.setattr(auth_mod, "_http_get_json", fake_get)

    assert auth_mod.get_session_token("ghu_b", now=1.0) == "tid_one"
    # 50 s before expiry — within the 60 s safety window → refresh.
    assert auth_mod.get_session_token("ghu_b", now=50.0) == "tid_two"


def test_get_session_token_translates_401_to_auth_error(monkeypatch):
    def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        raise _http_error(401)

    monkeypatch.setattr(auth_mod, "_http_get_json", fake_get)

    with pytest.raises(auth_mod.CopilotAuthError, match="copilot-login"):
        auth_mod.get_session_token("ghu_bad")


def test_get_session_token_passes_through_5xx(monkeypatch):
    def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        raise _http_error(503)

    monkeypatch.setattr(auth_mod, "_http_get_json", fake_get)

    with pytest.raises(urllib.error.HTTPError):
        auth_mod.get_session_token("ghu_x")
