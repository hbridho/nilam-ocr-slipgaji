from functools import lru_cache
from typing import Self

from pydantic import AliasChoices, Field, model_validator

from ocr_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    port: int = 8037

    # `slip_identity` memuat model terlatih dari slip_ml; `mock` hanya lokal.
    identity_backend: str = "slip_identity"

    # Ambang lolos, pada P(slip gaji): lolos kalau proba_slip_gaji >= ambang. Kosong = ambang yang ikut
    # tersimpan bersama model (0,47), yaitu ambang tertinggi yang masih meloloskan >= 97% slip gaji asli saat
    # validasi. IDENTITY_THRESHOLD (nama NPWP: GUARDRAILS_THRESHOLD); nama lama IDENTITY_REJECT_THRESHOLD masih
    # dibaca, artinya sama.
    identity_threshold: float | None = Field(
        None, gt=0, lt=1, validation_alias=AliasChoices("identity_threshold", "identity_reject_threshold")
    )

    # Ambang boleh dimiliki Orkestrasi pusat: GET IDENTITY_THRESHOLD_URL + PATH menjawab
    # {"threshold": 0.55} (kunci lama reject_threshold juga diterima), di-cache IDENTITY_THRESHOLD_CACHE_SECONDS.
    #
    # Hanya pemeriksaan ini yang punya setelan itu, dari ketiga guardrail: skornya P(slip gaji), dan
    # itulah satu-satunya dari tiga yang titik operasinya wajar digeser dari luar. Batas `blank`
    # adalah panjang teks, dan skala gerbang mutu berlawanan arah — satu angka `reject_threshold`
    # untuk ketiganya akan menggeser titik operasi dua yang lain tanpa ada yang menyadari.
    identity_threshold_url: str | None = None
    identity_threshold_path: str = "/v1/thresholds/guardrails"
    identity_threshold_api_key: str | None = None
    identity_threshold_timeout_seconds: float = Field(2.0, gt=0)
    identity_threshold_cache_seconds: float = Field(60.0, ge=0)

    # The model from GCS instead of the JSON baked in the image (downloaded at start into MODELS_DIR, see
    # ocr_common/clients/models.py and slip_ml/models.py). Fixed path per model, overwritten on upload; the
    # container takes the new one when it restarts. SHA-256 optional: without it the download is checked against
    # the SHA-256 recorded at upload. Credentials: AZURE_* / GCP_* (the GCS identity).
    #   gs://gc-bribrain-dev-gcs-ocr-nilam-01/nilam-ocr-slipgaji/guardrails/is_slip_gaji_doc_confidence/v1/is_slip_gaji_doc_confidence_v1.json
    identity_model_gcs_uri: str | None = None
    identity_model_sha256: str | None = None

    @model_validator(mode="after")
    def _guard_identity(self) -> Self:
        self.check_model_uri("identity_model_gcs_uri", self.identity_model_gcs_uri)
        self.reject_mock_backend_outside_local(identity_backend=self.identity_backend)
        self.reject_localhost_outside_local(identity_threshold_url=self.identity_threshold_url)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
