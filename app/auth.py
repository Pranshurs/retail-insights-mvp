"""Owner login for the dashboard, and the API key for machine clients (POS agent).

Plug-and-play: on first visit the dashboard asks the owner to set a password (stored as a
salted scrypt hash). The session-signing secret is SECRET_KEY if set, otherwise generated
once and kept in the database. Every dashboard form carries a per-session CSRF token.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.db import Setting


def _get(db: Session, key: str) -> str | None:
    s = db.get(Setting, key)
    return s.value if s else None


def _put(db: Session, key: str, value: str) -> None:
    s = db.get(Setting, key)
    if s:
        s.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


def session_secret(db: Session) -> str:
    env = os.getenv("SECRET_KEY", "").strip()
    if env:
        return env
    stored = _get(db, "session_secret")
    if stored is None:
        stored = secrets.token_urlsafe(48)
        _put(db, "session_secret", stored)
    return stored


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def owner_exists(db: Session) -> bool:
    return _get(db, "owner_password") is not None


def set_owner_password(db: Session, password: str) -> None:
    if len(password) < 10:
        raise ValueError("use at least 10 characters")
    salt = secrets.token_bytes(16)
    _put(db, "owner_password", salt.hex() + "$" + _hash(password, salt))


def check_owner_password(db: Session, password: str) -> bool:
    stored = _get(db, "owner_password")
    if not stored:
        return False
    salt_hex, digest = stored.split("$", 1)
    return hmac.compare_digest(_hash(password, bytes.fromhex(salt_hex)), digest)


class LoginThrottle:
    """At most ``limit`` failed logins per client per ``window`` seconds."""

    def __init__(self, limit: int = 5, window: float = 300.0):
        self.limit, self.window = limit, window
        self.fails: dict[str, deque] = defaultdict(deque)

    def blocked(self, client: str) -> bool:
        q = self.fails[client]
        while q and time.monotonic() - q[0] > self.window:
            q.popleft()
        return len(q) >= self.limit

    def failed(self, client: str) -> None:
        self.fails[client].append(time.monotonic())

    def reset(self, client: str) -> None:
        self.fails.pop(client, None)


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


def check_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(expected, submitted or ""):
        raise HTTPException(status_code=403, detail="form expired; reload the page and try again")


def check_api_key(provided: str) -> None:
    key = os.getenv("API_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="API_KEY is not configured on the server")
    if not hmac.compare_digest(provided.encode(), key.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
