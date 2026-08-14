"""storage.py -- thin direct-httpx wrapper around Supabase Storage's REST
API, same "no provider SDK, direct HTTP" convention used elsewhere in this
workspace for Ollama. Works identically against the local `supabase start`
stack and a real Supabase project -- same API, different base URL, which is
exactly what makes local testing here prod-parity rather than a stand-in.
"""
from __future__ import annotations

import httpx

from .config import settings

_EXT_BY_CONTENT_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


def object_path(artifact_id: str, content_type: str) -> str:
    return artifact_id + _EXT_BY_CONTENT_TYPE.get(content_type, "")


def public_url(storage_path: str) -> str:
    return f"{settings.supabase_url}/storage/v1/object/public/{settings.artifacts_bucket}/{storage_path}"


def upload(storage_path: str, content: bytes, content_type: str) -> None:
    resp = httpx.post(
        f"{settings.supabase_url}/storage/v1/object/{settings.artifacts_bucket}/{storage_path}",
        content=content,
        headers={
            "Authorization": f"Bearer {settings.supabase_service_key}",
            "apikey": settings.supabase_service_key,
            "Content-Type": content_type,
        },
        timeout=60,
    )
    resp.raise_for_status()


def download(storage_path: str) -> bytes:
    resp = httpx.get(
        f"{settings.supabase_url}/storage/v1/object/{settings.artifacts_bucket}/{storage_path}",
        headers={
            "Authorization": f"Bearer {settings.supabase_service_key}",
            "apikey": settings.supabase_service_key,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def delete(storage_path: str) -> None:
    resp = httpx.delete(
        f"{settings.supabase_url}/storage/v1/object/{settings.artifacts_bucket}/{storage_path}",
        headers={
            "Authorization": f"Bearer {settings.supabase_service_key}",
            "apikey": settings.supabase_service_key,
        },
        timeout=30,
    )
    resp.raise_for_status()
