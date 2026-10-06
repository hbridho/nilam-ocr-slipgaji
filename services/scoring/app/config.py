from functools import lru_cache
from typing import Self

from pydantic import model_validator

from ocr_common.config import PipelineSettings


class Settings(PipelineSettings):
    port: int = 8033

    # `conf_v2` memuat model terlatih dari slip_ml (conf.json); `mock` hanya untuk ENVIRONMENT=local.
    scoring_backend: str = "conf_v2"

    # The model from GCS instead of the JSON baked in the image (downloaded at start into MODELS_DIR, see
    # ocr_common/clients/models.py and slip_ml/models.py). Fixed path per model, overwritten on upload; the
    # container takes the new one when it restarts. SHA-256 optional: without it the download is checked against
    # the SHA-256 recorded at upload. Credentials: AZURE_* / GCP_* (the GCS identity).
    #   gs://gc-bribrain-dev-gcs-ocr-nilam-01/nilam-ocr-slipgaji/scoring/field_confidence/v1/field_confidence_v1.json
    scoring_model_gcs_uri: str | None = None
    scoring_model_sha256: str | None = None

    @model_validator(mode="after")
    def _guard_scoring(self) -> Self:
        self.check_model_uri("scoring_model_gcs_uri", self.scoring_model_gcs_uri)
        self.reject_mock_backend_outside_local(scoring_backend=self.scoring_backend)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
