"""GitHub Copilot device-flow OAuth + session-token swap.

GitHub Copilot's chat endpoint (``api.githubcopilot.com``) does not
accept regular ``gho_*`` PATs. Authentication is a two-step process
used by the official editor clients:

1. **Device-flow OAuth** against the Copilot OAuth app (client id
   ``Iv1.b507a08c87ecfe98``) yields a long-lived ``ghu_*`` token that
   represents the user's Copilot subscription. We persist this once
   per machine at ``~/.flowforge/copilot-oauth.json`` (0600).
2. **Session token swap**: each time the model is invoked, the
   ``ghu_*`` token is exchanged at
   ``https://api.github.com/copilot_internal/v2/token`` for a
   short-lived (~30 min) ``tid_*`` token, which is the actual bearer
   used against ``api.githubcopilot.com``.

This is the same pattern used by the VS Code Copilot Chat extension,
``aider``, ``copilot-api``, and similar community clients. It is not
officially documented or sanctioned by GitHub; users opt in via
``swe-forge setup`` choosing the Copilot provider.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Public client id used by the GitHub Copilot Chat editor extension.
# This is the only OAuth app whose tokens are accepted by
# ``/copilot_internal/v2/token``.
COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
SESSION_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"

OAUTH_TOKEN_PATH = Path.home() / ".flowforge" / "copilot-oauth.json"

EDITOR_HEADERS = {
    "Editor-Version": "vscode/1.95.0",
    "Editor-Plugin-Version": "copilot-chat/0.20.0",
    "User-Agent": "GitHubCopilotChat/0.20.0",
}


class CopilotAuthError(RuntimeError):
    """Raised when Copilot authentication fails."""


@dataclass
class _SessionTokenCacheEntry:
    token: str
    expires_at: float


_SESSION_CACHE: dict[str, _SessionTokenCacheEntry] = {}


def _http_post_form(url: str, data: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Accept": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _save_oauth_token(token: str) -> None:
    """Persist OAuth token atomically with mode 0o600 from creation."""
    parent = OAUTH_TOKEN_PATH.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".copilot-oauth.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"oauth_token": token}, fh)
        os.replace(tmp_path, OAUTH_TOKEN_PATH)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def load_oauth_token() -> str | None:
    """Return the cached ``ghu_*`` OAuth token if present, else ``None``."""
    if not OAUTH_TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(OAUTH_TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = data.get("oauth_token")
    return token if isinstance(token, str) and token else None


def device_login(*, output: Any = None, poll_interval_override: float | None = None) -> str:
    """Run the GitHub device-flow against the Copilot OAuth app.

    Prints the user code and verification URL to ``output`` (defaults
    to ``sys.stderr``), polls until the user authorizes or rejects,
    and persists the resulting OAuth token.

    Returns the new ``ghu_*`` token.
    """
    out = output if output is not None else sys.stderr

    init = _http_post_form(
        DEVICE_CODE_URL,
        {"client_id": COPILOT_CLIENT_ID, "scope": "read:user"},
        headers=EDITOR_HEADERS,
    )
    device_code = init["device_code"]
    user_code = init["user_code"]
    verification_uri = init.get("verification_uri", "https://github.com/login/device")
    interval = (
        poll_interval_override if poll_interval_override is not None else float(init.get("interval", 5))
    )
    expires_in = float(init.get("expires_in", 900))

    print("\n━━━ GitHub Copilot Login ━━━", file=out)
    print(f"  Open: {verification_uri}", file=out)
    print(f"  Enter code: {user_code}", file=out)
    print("  Waiting for authorization...", file=out, flush=True)

    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        try:
            resp = _http_post_form(
                ACCESS_TOKEN_URL,
                {
                    "client_id": COPILOT_CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers=EDITOR_HEADERS,
            )
        except urllib.error.HTTPError as exc:
            raise CopilotAuthError(f"device-flow poll failed: HTTP {exc.code}") from exc

        if "access_token" in resp:
            token = resp["access_token"]
            _save_oauth_token(token)
            print("✅ Copilot login successful.\n", file=out, flush=True)
            return token

        err = resp.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        if err in ("access_denied", "expired_token"):
            raise CopilotAuthError(f"device-flow rejected: {err}")
        # Unknown error — surface it.
        raise CopilotAuthError(f"device-flow error: {resp}")

    raise CopilotAuthError("device-flow timed out before authorization")


def ensure_oauth_token(*, interactive: bool = True) -> str:
    """Return a usable OAuth token, prompting via device-flow if missing."""
    token = load_oauth_token()
    if token:
        return token
    if not interactive:
        raise CopilotAuthError(
            "No Copilot OAuth token cached. Run `swe-forge copilot-login` first."
        )
    return device_login()


def get_session_token(oauth_token: str, *, now: float | None = None) -> str:
    """Swap an OAuth token for a short-lived Copilot session token.

    Caches per OAuth token within the process; refreshes 60 s before
    expiry. Raises :class:`CopilotAuthError` on 401/404 (token rejected
    by Copilot — usually means the user isn't subscribed or the token
    is from the wrong OAuth app).
    """
    current = now if now is not None else time.time()
    cached = _SESSION_CACHE.get(oauth_token)
    if cached and cached.expires_at - 60 > current:
        return cached.token

    try:
        payload = _http_get_json(
            SESSION_TOKEN_URL,
            headers={
                "Authorization": f"token {oauth_token}",
                **EDITOR_HEADERS,
            },
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise CopilotAuthError(
                f"Copilot rejected OAuth token (HTTP {exc.code}). "
                "Re-run `swe-forge copilot-login`."
            ) from exc
        raise

    token = payload["token"]
    expires_at = float(payload.get("expires_at", current + 1500))
    _SESSION_CACHE[oauth_token] = _SessionTokenCacheEntry(token=token, expires_at=expires_at)
    return token


def clear_session_cache() -> None:
    """Drop in-process session-token cache (test hook)."""
    _SESSION_CACHE.clear()
