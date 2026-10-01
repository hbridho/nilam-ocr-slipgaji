from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator

from ocr_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    port: int = 8034

    # PDF dengan halaman lebih banyak ditolak 400 di sini, sebelum satu tahap pun berjalan. Bentuk
    # yang paling sering diunggah adalah tiga bulan dalam satu berkas, jadi batasnya lebih longgar
    # daripada dokumen identitas. MAX_UPLOAD_BYTES (413) juga diperiksa di sini.
    max_document_pages: int = Field(12, ge=1)

    extraction_service_url: str = "http://127.0.0.1:8030"
    extraction_api_key: str | None = None
    extraction_timeout_seconds: float = 10.0
    structuring_service_url: str = "http://127.0.0.1:8032"
    structuring_api_key: str | None = None
    structuring_timeout_seconds: float = 10.0
    scoring_service_url: str = "http://127.0.0.1:8033"
    scoring_api_key: str | None = None
    scoring_timeout_seconds: float = 10.0
    pipeline_retry_attempts: int = 3
    pipeline_retry_delay_seconds: float = 0.5
    # 15 detik seperti API spec NILAM ([07]). OCR slip gaji lebih berat daripada kartu satu halaman
    # (berkas tiga bulan = beberapa detik per halaman), jadi sebagian permintaan dijawab 202 dan
    # hasilnya menyusul lewat callback / GET. Naikkan di sini bila Orkestrasi pusat menyetujuinya.
    pipeline_wait_seconds: float = Field(15.0, ge=0)
    pipeline_poll_interval_seconds: float = Field(0.5, gt=0)

    @model_validator(mode="after")
    def _guard_orchestrator(self) -> Self:
        # Ketiganya selalu: GET /v1/extract-ocr/{request_id} membaca tahap-tahap itu bahkan ketika
        # POST tidak menunggu.
        self.reject_localhost_outside_local(
            extraction_service_url=self.extraction_service_url,
            structuring_service_url=self.structuring_service_url,
            scoring_service_url=self.scoring_service_url,
        )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
