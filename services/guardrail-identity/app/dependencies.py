"""Composition root: satu-satunya tempat yang memutuskan implementasi mana yang berjalan.

Setiap `get_*` di sini adalah yang diambil route lewat `Depends(...)` dan yang diganti tes lewat
`app.dependency_overrides[...]`. Tidak ada bagian lain di service ini yang membangun objek-objek ini."""

from functools import lru_cache

from ocr_common.clients.remote import RemoteModelClient
from ocr_common.registry import Factory, build_backend

from app.clients.reject_threshold import RejectThreshold, default_threshold
from app.config import Settings, get_settings
from app.ml.base import IdentityCheck
from app.ml.mock import MockIdentityCheck
from app.ml.slip_identity import build_identity_check

# IDENTITY_BACKEND -> cara membangunnya. Tambahkan backend di sini dan, kalau butuh setelan, di config.py.
BACKENDS: dict[str, Factory[IdentityCheck]] = {
    "mock": lambda settings: MockIdentityCheck(),
    "slip_identity": lambda settings: build_identity_check(),
}


# --- ML ---------------------------------------------------------------------------


@lru_cache
def get_identity_check() -> IdentityCheck:
    settings: Settings = get_settings()
    return build_backend(BACKENDS, settings.identity_backend, settings, "identity model")


# --- clients ------------------------------------------------------------------------------


@lru_cache
def get_reject_threshold() -> RejectThreshold:
    """Ambang dari Orkestrasi pusat (satu per proses: dialah yang memegang cache)."""
    settings: Settings = get_settings()
    client = None
    if settings.identity_threshold_url:
        headers = {"X-API-Key": settings.identity_threshold_api_key} if settings.identity_threshold_api_key else None
        client = RemoteModelClient(
            settings.identity_threshold_url,
            settings.identity_threshold_timeout_seconds,
            name="orchestrator reject threshold",
            headers=headers,
        )
    return RejectThreshold(
        client,
        settings.identity_threshold_path,
        default_threshold(settings.identity_reject_threshold, get_identity_check()),
        cache_seconds=settings.identity_threshold_cache_seconds,
    )


# --- services (murah dibangun: satu per permintaan) ------------------------------------------


def get_identity_service():
    from app.services.identity_service import IdentityService

    return IdentityService(get_identity_check(), get_settings(), get_reject_threshold())
