"""Supabase user authentication for human-facing Task Queue routes.

Worker and producer credentials are deliberately a separate milestone. A
browser access token proves the human user; it must not be copied into a
long-running worker process.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    name: str | None = None
    avatar_url: str | None = None


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
