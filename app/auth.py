"""Login/session auth (FR-01), replacing the client-trusted role dropdown.

Mock user directory in data/users.json (PBKDF2-hashed passwords, no plaintext
at rest). A successful login gets an HMAC-signed, timestamped session token
in an httponly cookie; role is read from that session server-side, so a
client can no longer just claim to be "manager" the way the old ?role=
query/form field allowed.

Deliberately stdlib-only (hashlib + hmac) to avoid adding a dependency for
what is, in this MVP, still a mock login backed by a JSON file.
"""
import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import SESSION_SECRET, SESSION_TTL_HOURS, USERS_FILE

COOKIE_NAME = "fleetops_session"
PBKDF2_ITERATIONS = 200_000


@dataclass
class User:
    username: str
    role: str
    name: str


def _load_users() -> dict[str, dict]:
    if not USERS_FILE.exists():
        return {}
    return {u["username"]: u for u in json.loads(USERS_FILE.read_text(encoding="utf-8"))}


_USERS = _load_users()


def hash_password(password: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
    return dk.hex()


def verify_password(username: str, password: str) -> User | None:
    record = _USERS.get(username)
    if not record:
        return None
    expected = hash_password(password, record["salt"])
    if not hmac.compare_digest(expected, record["password_hash"]):
        return None
    return User(username=username, role=record["role"], name=record["name"])


def _sign(payload: bytes) -> str:
    return hmac.new(SESSION_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def create_session_token(user: User) -> str:
    body = json.dumps(
        {"u": user.username, "r": user.role, "n": user.name, "exp": time.time() + SESSION_TTL_HOURS * 3600}
    ).encode("utf-8")
    b64 = base64.urlsafe_b64encode(body).decode("ascii")
    return f"{b64}.{_sign(body)}"


def _read_session_token(token: str) -> User | None:
    try:
        b64, sig = token.split(".", 1)
        body = base64.urlsafe_b64decode(b64.encode("ascii"))
    except (ValueError, binascii.Error):
        return None
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return User(username=data["u"], role=data["r"], name=data["n"])


def get_current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE_NAME)
    user = _read_session_token(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_current_user_optional(request: Request) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    return _read_session_token(token) if token else None
