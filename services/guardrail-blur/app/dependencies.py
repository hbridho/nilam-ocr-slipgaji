"""Composition root: satu-satunya tempat yang memutuskan implementasi mana yang berjalan.

Setiap `get_*` di sini adalah yang diambil route lewat `Depends(...)` dan yang diganti tes lewat
`app.dependency_overrides[...]`. Tidak ada bagian lain di service ini yang membangun objek-objek ini."""

from functools import lru_cache

from ocr_common.registry import Factory, build_backend

from app.config import Settings, get_settings
from app.ml.base import BlurCheck
from app.ml.mock import MockBlurCheck
from app.ml.slip_blur import build_blur_check

# BLUR_BACKEND -> cara membangunnya. Tambahkan backend di sini dan, kalau butuh setelan, di config.py.


def _with_model(settings, build):
    """Point slip_ml at the model file (guard_quality.json: baked in the image, or downloaded from
    BLUR_MODEL_GCS_URI at start), then build the backend."""
    from ocr_common.clients.models import model_file

    from slip_ml import models

    models.use(
        models.QUALITY,
        model_file(models.baked(models.QUALITY), settings.blur_model_gcs_uri, settings.blur_model_sha256, settings),
    )
    return build()


BACKENDS: dict[str, Factory[BlurCheck]] = {
    "mock": lambda settings: MockBlurCheck(),
    "slip_blur": lambda settings: _with_model(settings, build_blur_check),
}


# --- ML ---------------------------------------------------------------------------


@lru_cache
def get_blur_check() -> BlurCheck:
    settings: Settings = get_settings()
    return build_backend(BACKENDS, settings.blur_backend, settings, "blur check model")


# --- services (murah dibangun: satu per permintaan) ------------------------------------------


def get_blur_service():
    from app.services.blur_service import BlurService

    return BlurService(get_blur_check(), get_settings())
