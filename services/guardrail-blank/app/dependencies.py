"""Composition root: satu-satunya tempat yang memutuskan implementasi mana yang berjalan.

Setiap `get_*` di sini adalah yang diambil route lewat `Depends(...)` dan yang diganti tes lewat
`app.dependency_overrides[...]`. Tidak ada bagian lain di service ini yang membangun objek-objek ini."""

from functools import lru_cache

from ocr_common.registry import Factory, build_backend

from app.config import Settings, get_settings
from app.ml.base import BlankCheck
from app.ml.mock import MockBlankCheck
from app.ml.slip_blank import build_blank_check

# BLANK_BACKEND -> cara membangunnya. Tambahkan backend di sini dan, kalau butuh setelan, di config.py.
BACKENDS: dict[str, Factory[BlankCheck]] = {
    "mock": lambda settings: MockBlankCheck(),
    "slip_blank": lambda settings: build_blank_check(),
}


# --- ML ---------------------------------------------------------------------------


@lru_cache
def get_blank_check() -> BlankCheck:
    settings: Settings = get_settings()
    return build_backend(BACKENDS, settings.blank_backend, settings, "blank check")


# --- services (murah dibangun: satu per permintaan) ------------------------------------------


def get_blank_service():
    from app.services.blank_service import BlankService

    return BlankService(get_blank_check(), get_settings())
