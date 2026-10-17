"""Human authentication and segregated producer/worker machine credentials."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .services import worker_key_service

_bearer = HTTPBearer(auto_error=False)
_service_key = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    name: str | None = None
    avatar_url: str | None = None
    # Set only for a self-service worker key: the user who owns that key.
    owner_user_id: str | None = None


# Users' worker keys: sha256(key) -> (owner, expiry as epoch seconds). Only
# matches are cached, and for at most WORKER_KEY_CACHE_SECONDS, so revoking a
# key or suspending its owner takes effect within a minute; misses are never
# cached, so a brand-new key works at once. Keyed by the hash, never the key.
WORKER_KEY_CACHE_SECONDS = 60
_worker_key_cache: dict[str, tuple[worker_key_service.KeyOwner, float]] = {}


def clear_worker_key_cache() -> None:
    _worker_key_cache.clear()


def _now() -> float:
    return time.time()


def public_auth_config() -> dict:
    """Return only values that are explicitly safe to expose to a browser."""
    return {
        "enabled": settings.auth_enabled,
        "supabase_url": settings.supabase_url if settings.auth_enabled else None,
        "supabase_publishable_key": (
            settings.supabase_publishable_key if settings.auth_enabled else None
        ),
    }


async def _fetch_supabase_user(access_token: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
                headers={
                    "apikey": settings.supabase_publishable_key,
                    "Authorization": f"Bearer {access_token}",
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Identity service is temporarily unavailable") from exc

    if response.status_code != 200:
        raise HTTPException(
            401,
            "Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return response.json()


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthUser:
    # The explicit off switch preserves local development and existing
    # machine clients until the deployment is activated. Production must set
    # AUTH_ENABLED=true to protect the human-facing routes below.
    if not settings.auth_enabled:
        return AuthUser(id="auth-disabled", email="local-admin@localhost")

    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            401,
            "Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = await _fetch_supabase_user(credentials.credentials)
    email = str(payload.get("email") or "").strip()
    metadata = payload.get("user_metadata") or {}
    return AuthUser(
        id=str(payload.get("id") or ""),
        email=email,
        name=metadata.get("full_name") or metadata.get("name"),
        avatar_url=metadata.get("avatar_url") or metadata.get("picture"),
    )


async def require_admin(user: AuthUser = Depends(current_user)) -> AuthUser:
    if settings.auth_enabled and user.email.casefold() not in settings.admin_emails:
        raise HTTPException(403, "This account does not have Task Queue admin access")
    return user


def _name_for_key(raw_key: str, configured_hashes: dict[str, str]) -> str | None:
    digest = hashlib.sha256(raw_key.encode()).hexdigest()
    for known, name in configured_hashes.items():
        if hmac.compare_digest(digest, known):
            return name
    return None


def _machine_user(kind: str, name: str) -> AuthUser:
    return AuthUser(id=f"{kind}:{name}", email=f"{name}@{kind}")


def _require_machine_key(
    kind: str,
    api_key: str | None,
    configured_hashes: dict[str, str],
) -> AuthUser:
    if api_key:
        name = _name_for_key(api_key, configured_hashes)
        if name is None:
            raise HTTPException(401, "Invalid API key")
        return _machine_user(kind, name)
    if settings.machine_auth_required:
        raise HTTPException(401, "API key required")
    return _machine_user(kind, "rollout-anonymous")


async def require_producer(api_key: str | None = Depends(_service_key)) -> AuthUser:
    """Require a producer key, subject to the temporary rollout switch."""
    return _require_machine_key("producer", api_key, settings.service_key_hashes)


def _user_worker_key(db: Session, raw_key: str) -> worker_key_service.KeyOwner | None:
    digest = worker_key_service.hash_key(raw_key)
    now = _now()
    cached = _worker_key_cache.get(digest)
    if cached and cached[1] > now:
        return cached[0]
    _worker_key_cache.pop(digest, None)
    try:
        owner = worker_key_service.find_active_key(db, raw_key)
    except SQLAlchemyError as exc:
        # An outage is not a bad key: a 401 would make workers give up.
        raise HTTPException(503, "Worker key check is temporarily unavailable") from exc
    if owner is not None:
        _worker_key_cache[digest] = (owner, now + WORKER_KEY_CACHE_SECONDS)
    return owner


def require_worker(
    api_key: str | None = Depends(_service_key),
    db: Session = Depends(get_db),
) -> AuthUser:
    """Require a worker key: an environment WORKER_API_KEYS key, or an active
    self-service key whose owner is approved. Human and producer credentials
    never qualify, and a user's worker key never qualifies as a producer."""
    if not api_key:
        return _require_machine_key("worker", api_key, settings.worker_key_hashes)
    name = _name_for_key(api_key, settings.worker_key_hashes)
    if name is not None:
        return _machine_user("worker", name)
    owner = _user_worker_key(db, api_key)
    if owner is None:
        raise HTTPException(401, "Invalid API key")
    return AuthUser(id=f"worker-key:{owner.key_id}", email=f"{owner.key_id}@worker-key",
                    owner_user_id=owner.user_id)


async def require_admin_or_service(
    api_key: str | None = Depends(_service_key),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthUser:
    """Admin browser session OR a configured machine key (X-API-Key).

    Machine clients (other apps) must never borrow a human session; they get
    their own revocable key instead (see SERVICE_API_KEYS)."""
    if api_key:
        name = _name_for_key(api_key, settings.service_key_hashes)
        if name is None:
            raise HTTPException(401, "Invalid API key")
        return _machine_user("producer", name)
    if not settings.auth_enabled:
        return AuthUser(id="auth-disabled", email="local-admin@localhost")
    return await require_admin(await current_user(credentials))
