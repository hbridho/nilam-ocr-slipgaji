from functools import lru_cache
from typing import Self

from pydantic import model_validator

from ocr_common.config import PipelineSettings


class Settings(PipelineSettings):
    port: int = 8033

    # `conf_v2` memuat model terlatih dari slip_ml (conf.json); `mock` hanya untuk ENVIRONMENT=local.
    scoring_backend: str = "conf_v2"

    @model_validator(mode="after")
    def _guard_scoring(self) -> Self:
        self.reject_mock_backend_outside_local(scoring_backend=self.scoring_backend)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
