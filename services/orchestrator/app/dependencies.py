"""Composition root: satu-satunya tempat yang memutuskan implementasi mana yang berjalan.

Setiap `get_*` di sini adalah yang diambil route lewat `Depends(...)` dan yang diganti tes lewat
`app.dependency_overrides[...]`. Tidak ada bagian lain di service ini yang membangun objek-objek ini."""

from functools import lru_cache

from fastapi import Depends

from app.clients.extraction import ExtractionJobClient, build_extraction_client
from app.clients.stages import StageStatusClient, build_stage_status_clients
from app.config import Settings, get_settings
from app.services.extract_service import ExtractOcrService
from app.services.pipeline_waiter import PipelineWaiter

# --- klien HTTP ke service lain (satu per proses, ditutup di main.lifespan) -------------
# Tidak ada klien guardrails di sini: model guardrail slip gaji membaca teks OCR, jadi tahap OCR
# yang memanggilnya. Orchestrator hanya meneruskan `pipeline_name_sequence` dan ambang permintaan, lalu melaporkan
# putusannya kembali sebagai `guardrails: 0/1` di kontrak.


@lru_cache
def get_extraction_client() -> ExtractionJobClient:
    return build_extraction_client(get_settings())


@lru_cache
def get_stage_status_clients() -> tuple[StageStatusClient, ...]:
    return build_stage_status_clients(get_settings())


@lru_cache
def get_pipeline_waiter() -> PipelineWaiter:
    return PipelineWaiter(get_stage_status_clients(), poll_interval=get_settings().pipeline_poll_interval_seconds)


# Endpoint pengujian (TESTING_ENDPOINTS): pemeriksaan dan penantian yang sama, pada endpoint `-test`
# milik tiap tahap (lihat ocr_common.testing_endpoints).


@lru_cache
def get_testing_extraction_client() -> ExtractionJobClient:
    return build_extraction_client(get_settings(), testing=True)


@lru_cache
def get_testing_stage_status_clients() -> tuple[StageStatusClient, ...]:
    return build_stage_status_clients(get_settings(), testing=True)


@lru_cache
def get_testing_pipeline_waiter() -> PipelineWaiter:
    return PipelineWaiter(
        get_testing_stage_status_clients(), poll_interval=get_settings().pipeline_poll_interval_seconds
    )


# --- services (murah dibangun: satu per permintaan) ------------------------------------------


def get_extract_service(
    extraction: ExtractionJobClient = Depends(get_extraction_client),
    waiter: PipelineWaiter = Depends(get_pipeline_waiter),
    settings: Settings = Depends(get_settings),
) -> ExtractOcrService:
    return ExtractOcrService(extraction, waiter, settings)


def get_testing_extract_service(
    extraction: ExtractionJobClient = Depends(get_testing_extraction_client),
    waiter: PipelineWaiter = Depends(get_testing_pipeline_waiter),
    settings: Settings = Depends(get_settings),
) -> ExtractOcrService:
    return ExtractOcrService(extraction, waiter, settings)
