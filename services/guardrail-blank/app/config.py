from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator

from ocr_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    port: int = 8035

    # `slip_blank` memakai aturan yang sama dengan repo penelitian; `mock` hanya lokal.
    blank_backend: str = "slip_blank"

    # Batas panjang teks (setelah dipangkas) yang masih dihitung kosong. Kosong = angka yang ikut
    # tersimpan bersama gerbang mutu (20). Tidak ada ambang dari Orkestrasi pusat untuk pemeriksaan
    # ini: yang diputuskan bukan skor model melainkan panjang teks, dan angkanya tidak bergeser
    # bersama korpus seperti ambang model bergeser.
    blank_max_chars: int | None = Field(None, ge=0, le=10_000)

    # The model from GCS instead of the JSON baked in the image (downloaded at start into MODELS_DIR, see
    # ocr_common/clients/models.py and slip_ml/models.py). Fixed path per model, overwritten on upload; the
    # container takes the new one when it restarts. SHA-256 optional: without it the download is checked against
    # the SHA-256 recorded at upload. Credentials: AZURE_* / GCP_* (the GCS identity).
    #   gs://gc-bribrain-dev-gcs-ocr-nilam-01/nilam-ocr-slipgaji/guardrails/unreadable_doc_confidence/v1/unreadable_doc_confidence_v1.json
    blank_model_gcs_uri: str | None = None
    blank_model_sha256: str | None = None

    @model_validator(mode="after")
    def _guard_blank(self) -> Self:
        self.check_model_uri("blank_model_gcs_uri", self.blank_model_gcs_uri)
        self.reject_mock_backend_outside_local(blank_backend=self.blank_backend)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
