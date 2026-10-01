from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator

from ocr_common.config import PipelineSettings


class Settings(PipelineSettings):
    port: int = 8030

    # `api` mengirim tiap halaman ke layanan OCR (paddle6 / PP-OCRv6) — bawaan, dan tidak ada model
    # di dalam image ini. `rapidocr` menjalankan model ONNX lokal (butuh paket tambahan, image jauh
    # lebih besar); `mock` hanya untuk ENVIRONMENT=local.
    extraction_backend: str = "api"
    extraction_ocr_url: str | None = None
    extraction_ocr_endpoint: str = "/ocr"
    extraction_ocr_upload_field: str = "file"
    # Satu panggilan per halaman; layanan OCR bisa lambat ketika sedang antre, dan job ini toh
    # dibatasi PIPELINE_JOB_LEASE_SECONDS.
    extraction_ocr_timeout_seconds: float = Field(120.0, gt=0)
    extraction_render_dpi: int | None = None
    # Satu berkas slip gaji umumnya 1-3 halaman (tiga bulan); batas ini menjaga PDF gemuk tidak
    # menghabiskan satu worker OCR selama beberapa menit.
    extraction_max_pages: int = Field(20, gt=0)

    # Ketiga guardrail dipanggil SETELAH OCR (modelnya membaca teks OCR), BERSAMAAN, dan masing-masing
    # service sendiri yang bisa dimatikan sendiri. Yang dimatikan tidak ditanya dan tercatat di
    # `skipped`; dua yang lain tetap memutuskan.
    guardrail_blank_enabled: bool = True
    guardrail_blank_url: str = "http://127.0.0.1:8035"
    guardrail_blur_enabled: bool = True
    guardrail_blur_url: str = "http://127.0.0.1:8036"
    guardrail_identity_enabled: bool = True
    guardrail_identity_url: str = "http://127.0.0.1:8037"
    guardrails_api_key: str | None = None
    guardrails_timeout_seconds: float = Field(10.0, gt=0)
    # Guardrail yang menyala tetapi tidak menjawab -> dinilai tanpanya (tercatat di `unavailable`), bukan
    # job yang gagal. Menahan seluruh lalu lintas OCR karena satu penjaga tumbang lebih merugikan daripada
    # meloloskan sedikit dokumen — yang toh masih dinilai dua guardrail lain, structuring, dan skor
    # keyakinan. `false` membalik pilihan itu: galat guardrail menjadi job FAILED (503/504).
    guardrails_fail_open: bool = True

    structuring_service_url: str = "http://127.0.0.1:8032"
    structuring_api_key: str | None = None
    structuring_timeout_seconds: float = 10.0

    @model_validator(mode="after")
    def _guard_extraction(self) -> Self:
        self.reject_mock_backend_outside_local(extraction_backend=self.extraction_backend)
        if self.extraction_backend == "api" and not self.extraction_ocr_url:
            raise ValueError(
                "EXTRACTION_OCR_URL must be set when EXTRACTION_BACKEND=api: there is no OCR model in this image"
            )
        self.reject_localhost_outside_local(
            structuring_service_url=self.structuring_service_url,
            guardrail_blank_url=self.guardrail_blank_url if self.guardrail_blank_enabled else None,
            guardrail_blur_url=self.guardrail_blur_url if self.guardrail_blur_enabled else None,
            guardrail_identity_url=self.guardrail_identity_url if self.guardrail_identity_enabled else None,
        )
        # EXTRACTION_OCR_URL sengaja TIDAK diperiksa localhost-nya: layanan OCR bisa saja berjalan
        # sebagai sidecar di pod yang sama, dan itu justru alamat 127.0.0.1 yang sah.
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
