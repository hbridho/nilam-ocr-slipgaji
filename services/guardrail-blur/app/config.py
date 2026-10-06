from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator

from ocr_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    port: int = 8036

    # `slip_blur` memuat gerbang mutu terlatih dari slip_ml; `mock` hanya lokal.
    blur_backend: str = "slip_blur"

    # Ambang buram. Kosong = ambang yang ikut tersimpan bersama model (0,958), yaitu ambang
    # tertinggi yang masih meloloskan halaman yang benar-benar terbaca.
    #
    # Tidak ada ambang dari Orkestrasi pusat di sini, sengaja: skala skor gerbang mutu
    # (P(halaman terlalu rusak)) berlawanan arah dengan skala model identitas (P(slip gaji)), dan
    # satu angka `reject_threshold` untuk keduanya akan menggeser titik operasi salah satunya tanpa
    # ada yang menyadari.
    blur_threshold: float | None = Field(None, gt=0, lt=1)

    # The model from GCS instead of the JSON baked in the image (downloaded at start into MODELS_DIR, see
    # ocr_common/clients/models.py and slip_ml/models.py). Fixed path per model, overwritten on upload; the
    # container takes the new one when it restarts. SHA-256 optional: without it the download is checked against
    # the SHA-256 recorded at upload. Credentials: AZURE_* / GCP_* (the GCS identity).
    #   gs://gc-bribrain-dev-gcs-ocr-nilam-01/nilam-ocr-slipgaji/guardrails/unreadable_doc_confidence/v1/unreadable_doc_confidence_v1.json
    blur_model_gcs_uri: str | None = None
    blur_model_sha256: str | None = None

    @model_validator(mode="after")
    def _guard_blur(self) -> Self:
        self.check_model_uri("blur_model_gcs_uri", self.blur_model_gcs_uri)
        self.reject_mock_backend_outside_local(blur_backend=self.blur_backend)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
