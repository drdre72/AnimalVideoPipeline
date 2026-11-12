"""Google OAuth client handling for NAMINAL.

Imports only an explicit allowlist of Google configuration values — never
NewsRoom refresh tokens, channel tokens, RTMP or mail settings — into a
dedicated local token store excluded from Git. Authorization uses the
desktop loopback PKCE flow and is restricted to read-only YouTube scopes.
The authorized channel must match the configured NAMINAL identity before
any API call or future write; mismatches abort. Secrets are never logged.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
from pathlib import Path

from . import net
from .core import ROOT, now, settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DATA_API = "https://www.googleapis.com/youtube/v3"
GOOGLE_HOSTS = {"accounts.google.com", "oauth2.googleapis.com",
                "www.googleapis.com", "youtubeanalytics.googleapis.com"}
READ_SCOPES = ("https://www.googleapis.com/auth/youtube.readonly "
               "https://www.googleapis.com/auth/yt-analytics.readonly")

# Only these values may cross from other projects' files into our store.
ENV_ALLOWLIST = {"NEWSROOM_YT_CLIENT_ID": "client_id",
                 "NEWSROOM_YT_CLIENT_SECRET": "client_secret",
                 "NAMINAL_YT_CLIENT_ID": "client_id",
                 "NAMINAL_YT_CLIENT_SECRET": "client_secret"}
JSON_ALLOWLIST = {"client_id", "client_secret", "project_id"}


def secrets_path() -> Path:
    configured = settings().get("youtube", {}).get("secrets_file", "secrets/google.json")
    path = Path(configured)
    return path if path.is_absolute() else ROOT / path


def load_store() -> dict:
    path = secrets_path()
    if not path.is_file():
        return {}
    if os.stat(path).st_mode & 0o077:
        raise RuntimeError(f"insecure permissions on {path}; expected 600")
    return json.loads(path.read_text())


def save_store(store: dict) -> Path:
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(store, indent=2))
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def _import_env(path: Path) -> dict:
    imported = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition("=")
        key = key.strip()
        if key in ENV_ALLOWLIST and value.strip():
            imported[ENV_ALLOWLIST[key]] = value.strip().strip('"').strip("'")
    return imported


def _import_client_json(path: Path) -> dict:
    data = json.loads(path.read_text())
    inner = data.get("installed") or data.get("web") or {}
    return {key: inner[key] for key in JSON_ALLOWLIST if inner.get(key)}


def migrate(env: Path | None = None, client_json: Path | None = None) -> dict:
    """Copy allowlisted values only; never report secret values back."""
    if env is None and client_json is None:
        raise ValueError("migrate requires --env and/or --json")
    imported = {}
    if env is not None:
        imported.update(_import_env(Path(env)))
    if client_json is not None:
        imported.update(_import_client_json(Path(client_json)))
    if not imported:
        raise ValueError("no allowlisted Google credentials found in the sources")
    store = {**load_store(), **imported, "migrated_at": now()}
    path = save_store(store)
    return {"imported_keys": sorted(imported), "store": str(path),
            "note": "refresh tokens and channel settings were not copied"}


def _post(url: str, fields: dict, hosts: set[str] = GOOGLE_HOSTS):
    body = urllib.parse.urlencode(fields).encode()
    return net.bounded_request(url, hosts, name="Google OAuth", data=body,
                               headers={"Content-Type": "application/x-www-form-urlencoded"})


def _exchange(code: str, redirect_uri: str, verifier: str, store: dict) -> str:
    with _post(TOKEN_URL, {"client_id": store["client_id"],
                           "client_secret": store["client_secret"], "code": code,
                           "code_verifier": verifier, "redirect_uri": redirect_uri,
                           "grant_type": "authorization_code"}) as response:
        refresh = json.loads(response.read()).get("refresh_token")
    if not refresh:
        raise RuntimeError("Google did not return a refresh token")
    return refresh


def authorization_url(client_id: str, redirect_uri: str, state: str, verifier: str) -> str:
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": READ_SCOPES, "access_type": "offline",
        "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})


def channel_for_token(access_token: str) -> dict:
    url = DATA_API + "/channels?" + urllib.parse.urlencode(
        {"part": "id,snippet", "mine": "true"})
    with net.bounded_request(url, GOOGLE_HOSTS, name="YouTube API",
                             headers={"Authorization": f"Bearer {access_token}"}) as response:
        items = json.loads(response.read()).get("items", [])
    if not items:
        raise RuntimeError("OAuth account has no YouTube channel")
    channel = items[0]
    return {"id": channel["id"], "title": channel["snippet"]["title"]}


def authorize(timeout_s: int = 300, announce=print) -> dict:
    store = load_store()
    if not store.get("client_id") or not store.get("client_secret"):
        raise RuntimeError("no OAuth client stored; run `youtube migrate` first")
    verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
    result: dict[str, str] = {}
    auth_url = ""

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/start":
                self.send_response(302)
                self.send_header("Location", auth_url)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            params = urllib.parse.parse_qs(parsed.query)
            if parsed.path != "/callback" or params.get("state") != [state]:
                self.send_error(400, "invalid OAuth callback")
                return
            result["code"] = params.get("code", [""])[0]
            result["error"] = params.get("error", [""])[0]
            body = b"Authorization received. You can close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass  # never log the authorization code

    with http.server.HTTPServer(("127.0.0.1", 0), Callback) as server:
        redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
        auth_url = authorization_url(store["client_id"], redirect_uri, state, verifier)
        announce("Open this URL on this Mac to authorize the NAMINAL channel:")
        announce(f"http://127.0.0.1:{server.server_port}/start")
        deadline = time.monotonic() + timeout_s
        while "code" not in result and "error" not in result:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("YouTube authorization timed out")
            server.timeout = min(remaining, 1)
            server.handle_request()
    if result.get("error"):
        raise RuntimeError("YouTube authorization was denied")
    if not result.get("code"):
        raise RuntimeError("authorization returned no code")
    store["refresh_token"] = _exchange(result["code"], redirect_uri, verifier, store)
    save_store(store)
    channel = channel_for_token(access_token())
    store["channel_id"], store["channel_title"] = channel["id"], channel["title"]
    store["authorized_at"] = now()
    save_store(store)
    expected = settings().get("youtube", {}).get("channel_id") or ""
    if expected and expected != channel["id"]:
        save_store({k: v for k, v in store.items() if k != "refresh_token"})
        raise RuntimeError("authorized account is not the configured NAMINAL channel")
    return {"channel_id": channel["id"], "channel_title": channel["title"],
            "scopes": READ_SCOPES.split()}


def access_token() -> str:
    store = load_store()
    if not store.get("refresh_token"):
        raise RuntimeError("no authorized NAMINAL account; run `youtube auth` first")
    with _post(TOKEN_URL, {"client_id": store["client_id"],
                           "client_secret": store["client_secret"],
                           "refresh_token": store["refresh_token"],
                           "grant_type": "refresh_token"}) as response:
        return json.loads(response.read())["access_token"]


def verify_identity() -> dict:
    """Abort when the stored channel is not the configured NAMINAL channel."""
    store = load_store()
    expected = settings().get("youtube", {}).get("channel_id") or ""
    if expected and store.get("channel_id") and store["channel_id"] != expected:
        raise RuntimeError("stored OAuth channel is not the configured NAMINAL channel")
    channel = channel_for_token(access_token())
    if store.get("channel_id") and channel["id"] != store["channel_id"]:
        raise RuntimeError("OAuth account channel changed since authorization")
    if expected and channel["id"] != expected:
        raise RuntimeError("authorized account is not the configured NAMINAL channel")
    return channel


def status() -> dict:
    store = load_store()
    configured = settings().get("youtube", {})
    return {"client_configured": bool(store.get("client_id")),
            "authorized": bool(store.get("refresh_token")),
            "channel_id": store.get("channel_id"),
            "channel_title": store.get("channel_title"),
            "expected_channel": configured.get("channel_id") or None,
            "identity_matches": (not configured.get("channel_id")
                                 or store.get("channel_id") == configured.get("channel_id")),
            "store": str(secrets_path()),
            "upload_enabled": bool(configured.get("upload_enabled"))}
