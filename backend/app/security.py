"""Auth + abuse protection (§22).

- Demo-mode issues a short-lived READ-ONLY ("visitor") JWT for the seeded user.
- Owner login (OWNER_KEY) issues an "owner" JWT; every write endpoint requires it.
- Token-bucket rate limiting per client IP (in-memory; swap for Redis in prod).
- Input validation is handled by Pydantic schemas + explicit allowlists for
  sort/filter fields (no user input ever reaches raw SQL).
"""
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import (DEMO_MODE, JWT_SECRET, JWT_TTL_HOURS, LOGIN_ATTEMPTS_PER_MIN, OWNER_KEY,
                     RATE_LIMIT_PER_MIN)
from .database import SessionLocal
from .models import User

bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()
    return hmac.compare_digest(check, digest)


ROLE_OWNER = "owner"
ROLE_VISITOR = "visitor"


def issue_token(user: User, role: str = ROLE_VISITOR) -> str:
    now = int(time.time())
    payload = {"sub": str(user.id), "email": user.email, "role": role,
               "iat": now, "exp": now + JWT_TTL_HOURS * 3600}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def check_owner_key(candidate: str) -> bool:
    """Constant-time compare; always False when no OWNER_KEY is configured."""
    if not OWNER_KEY or not candidate:
        return False
    return hmac.compare_digest(candidate.encode(), OWNER_KEY.encode())


def _decode(creds: HTTPAuthorizationCredentials | None) -> dict:
    if creds is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        return jwt.decode(creds.credentials, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def _load_user(payload: dict) -> User:
    with SessionLocal() as db:
        try:
            user = db.get(User, int(payload.get("sub")))
        except (TypeError, ValueError):
            user = None
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown user")
    return user


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    """Any valid token (visitor or owner). Use for read endpoints."""
    return _load_user(_decode(creds))


def require_owner(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    """Owner token only: resets, approvals, backtests, deletions, Telegram tests."""
    payload = _decode(creds)
    if payload.get("role") != ROLE_OWNER:
        raise HTTPException(status_code=403, detail="Read-only access: owner login required")
    return _load_user(payload)


def require_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    """Everyday actions (save settings, watchlist, alerts, close/resume, start lab): any valid
    visitor or owner session. OPEN_BASIC_ACTIONS=0 makes these owner-only again."""
    from .config import OPEN_BASIC_ACTIONS
    if not OPEN_BASIC_ACTIONS:
        return require_owner(creds)
    return _load_user(_decode(creds))


# ---------------- rate limiting ----------------
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    q.append(now)


_login_hits: dict[str, deque] = defaultdict(deque)


def login_rate_limit(request: Request):
    """Much stricter bucket for owner-key guesses (brute-force protection)."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    q = _login_hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= LOGIN_ATTEMPTS_PER_MIN:
        raise HTTPException(status_code=429, detail="Too many login attempts; wait a minute")
    q.append(now)


# ---------------- validation helpers ----------------
SOLANA_ADDR_CHARS = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def valid_address(addr: str) -> bool:
    """Accept Solana base58 (32–44 chars) or EVM 0x hex addresses (§29)."""
    if addr.startswith("0x") and len(addr) == 42:
        return all(c in "0123456789abcdefABCDEF" for c in addr[2:])
    return 32 <= len(addr) <= 44 and all(c in SOLANA_ADDR_CHARS for c in addr)


ALLOWED_SORTS = {
    "trend": "trend", "fomo": "fomo", "risk": "risk",
    "volume": "volume", "liquidity": "liquidity", "mcap": "mcap",
    "price_change": "price_change", "social_growth": "social_growth",
    "holder_growth": "holder_growth", "age": "age",
}
