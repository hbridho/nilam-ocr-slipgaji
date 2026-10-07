"""Composition root: the one place that decides which implementation of each part runs.

Every `get_*` here is what the routes take through `Depends(...)` and what tests replace through
`app.dependency_overrides[...]`. Nothing else in the service builds these objects."""

from functools import lru_cache

from ocr_common.clients.guardrails import GuardrailEndpoint, GuardrailsFanout
from ocr_common.clients.remote import RemoteModelClient
from ocr_common.pipeline import (
    STAGE_OCR,
    NextStageClient,
    OutboxRelay,
    StagePipeline,
    StaleJobReaper,
    build_next_stage_client,
    build_outbox_relay,
    build_stage_pipeline,
    build_stale_job_reaper,
)
from ocr_common.registry import Factory, build_backend
from ocr_common.testing_endpoints import TESTING_TABLE_PREFIX, testing_path

from app.config import Settings, get_settings
from app.ml.api import ApiOcrEngine
from app.ml.base import OcrEngine
from app.ml.mock import MockOcrEngine
from app.services.extraction_service import ExtractionService
from app.services.guardrails_log import SqlGuardrailsLog
from app.services.job_service import ExtractionJobService

DB_TABLE_PREFIX = "ocr"


def _build_api(settings: Settings) -> ApiOcrEngine:
    return ApiOcrEngine(
        settings.extraction_ocr_url or "",
        endpoint=settings.extraction_ocr_endpoint,
        upload_field=settings.extraction_ocr_upload_field,
        timeout=settings.extraction_ocr_timeout_seconds,
        max_pages=settings.extraction_max_pages,
        render_dpi=settings.extraction_render_dpi,
    )


def _build_local(settings: Settings) -> OcrEngine:
    # Diimpor di dalam fungsi: rapidocr-onnxruntime tidak dipasang di image bawaan, dan mengimpornya
    # di tingkat modul akan membuat service gagal start hanya karena backend itu ADA di daftar.
    from app.ml.local import LocalOcrEngine

    return LocalOcrEngine(max_pages=settings.extraction_max_pages, render_dpi=settings.extraction_render_dpi)


# EXTRACTION_BACKEND -> cara membangunnya. Tambahkan backend di sini dan, kalau butuh setelan, di config.py.
OCR_BACKENDS: dict[str, Factory[OcrEngine]] = {
    "mock": lambda settings: MockOcrEngine(),
    "api": _build_api,
    "rapidocr": _build_local,
}


# --- ML ---------------------------------------------------------------------------


@lru_cache
def get_ocr_engine() -> OcrEngine:
    settings: Settings = get_settings()
    engine = build_backend(OCR_BACKENDS, settings.extraction_backend, settings, "extraction OCR")
    warmup = getattr(engine, "warmup", None)
    if warmup is not None:
        warmup()  # muat model OCR saat start; memuatnya pada permintaan pertama menambah ~3 detik
    return engine


# --- pipeline -----------------------------------------------------------------------


STRUCTURING_JOBS_PATH = "/v1/structuring/jobs"


def _next_stage(path: str, name: str) -> NextStageClient:
    settings = get_settings()
    return build_next_stage_client(
        settings,
        base_url=settings.structuring_service_url,
        api_key=settings.structuring_api_key,
        timeout=settings.structuring_timeout_seconds,
        path=path,
        name=name,
    )


@lru_cache
def get_next_stage() -> NextStageClient:
    return _next_stage(STRUCTURING_JOBS_PATH, "structuring service")


@lru_cache
def get_pipeline() -> StagePipeline:
    return build_stage_pipeline(
        get_settings(), stage=STAGE_OCR, table_prefix=DB_TABLE_PREFIX, next_stage=get_next_stage()
    )


@lru_cache
def get_relay() -> OutboxRelay | None:
    return build_outbox_relay(get_settings(), get_pipeline())


# The testing endpoints (TESTING_ENDPOINTS): the same pipeline on the testing_* tables, handing off to
# structuring's `-test` endpoint, without callbacks (see ocr_common.testing_endpoints).


@lru_cache
def get_testing_next_stage() -> NextStageClient:
    return _next_stage(testing_path(STRUCTURING_JOBS_PATH), "structuring service (testing)")


@lru_cache
def get_testing_pipeline() -> StagePipeline:
    return build_stage_pipeline(
        get_settings(),
        stage=STAGE_OCR,
        table_prefix=DB_TABLE_PREFIX,
        next_stage=get_testing_next_stage(),
        testing=True,
    )


@lru_cache
def get_testing_relay() -> OutboxRelay | None:
    return build_outbox_relay(get_settings(), get_testing_pipeline())


# --- services (cheap to build: one per request) ------------------------------------------


@lru_cache
def get_guardrails_client() -> GuardrailsFanout:
    """Ketiga guardrail (satu klien per service, satu per proses: ia memegang kumpulan koneksi).

    Tiap guardrail dinyalakan/dimatikan sendiri (`GUARDRAIL_<NAMA>_ENABLED`). Yang dimatikan tidak punya
    sambungan, tidak ditanya, dan tercatat di `skipped` laporan."""
    settings: Settings = get_settings()
    headers = {"X-API-Key": settings.guardrails_api_key or settings.api_key}

    def endpoint(name: str, enabled: bool, url: str) -> GuardrailEndpoint:
        if not enabled or not url:
            return GuardrailEndpoint(name, None)
        client = RemoteModelClient(
            url, settings.guardrails_timeout_seconds, name=f"guardrail-{name} service", headers=headers
        )
        return GuardrailEndpoint(name, client)

    return GuardrailsFanout(
        [
            endpoint("blank", settings.guardrail_blank_enabled, settings.guardrail_blank_url),
            endpoint("blur", settings.guardrail_blur_enabled, settings.guardrail_blur_url),
            endpoint("identity", settings.guardrail_identity_enabled, settings.guardrail_identity_url),
        ],
        fail_open=settings.guardrails_fail_open,
    )


def get_extraction_service() -> ExtractionService:
    return ExtractionService(get_ocr_engine(), get_settings(), get_guardrails_client())


@lru_cache
def get_guardrails_log() -> SqlGuardrailsLog | None:
    """Jawaban tiap guardrail ke `nilam_guardrails_results`; tanpa basis data tidak ada yang dicatat."""
    database_url = get_settings().database_url
    return SqlGuardrailsLog(database_url) if database_url else None


@lru_cache
def get_testing_guardrails_log() -> SqlGuardrailsLog | None:
    """Sama, untuk endpoint `-test`: `nilam_testing_guardrails_results`."""
    database_url = get_settings().database_url
    return SqlGuardrailsLog(database_url, table_prefix=TESTING_TABLE_PREFIX) if database_url else None


def _job_service(pipeline: StagePipeline, guardrails_log: SqlGuardrailsLog | None) -> ExtractionJobService:
    settings = get_settings()
    return ExtractionJobService(
        pipeline,
        get_extraction_service(),
        settings.max_upload_bytes,
        url_policy=settings.file_url_policy,
        simulate_delay=settings.is_local,
        handoff_by_reference=settings.pipeline_handoff_by_reference,
        guardrails_log=guardrails_log,
    )


def get_job_service() -> ExtractionJobService:
    return _job_service(get_pipeline(), get_guardrails_log())


def get_testing_job_service() -> ExtractionJobService:
    return _job_service(get_testing_pipeline(), get_testing_guardrails_log())


@lru_cache
def get_reaper() -> StaleJobReaper | None:
    return build_stale_job_reaper(get_settings(), get_pipeline(), get_job_service().resume)


@lru_cache
def get_testing_reaper() -> StaleJobReaper | None:
    return build_stale_job_reaper(get_settings(), get_testing_pipeline(), get_testing_job_service().resume)
