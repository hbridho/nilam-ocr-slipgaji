"""Composition root: the one place that decides which implementation of each part runs.

Every `get_*` here is what the routes take through `Depends(...)` and what tests replace through
`app.dependency_overrides[...]`. Nothing else in the service builds these objects."""

import logging
from functools import lru_cache

from ocr_common.pipeline import (
    STAGE_STRUCTURING,
    NextStageClient,
    OutboxRelay,
    StagePipeline,
    StageResults,
    StaleJobReaper,
    build_next_stage_client,
    build_outbox_relay,
    build_stage_pipeline,
    build_stage_results,
    build_stale_job_reaper,
)
from ocr_common.registry import Factory, build_backend
from ocr_common.testing_endpoints import testing_path

from app.config import Settings, get_settings
from app.ml.base import Structurer
from app.ml.mock import MockStructurer
from app.ml.slip_rules import SlipRulesStructurer
from app.services.job_service import StructuringJobService
from app.services.structuring_service import StructuringService

logger = logging.getLogger(__name__)

DB_TABLE_PREFIX = "structuring"


def _apply_llm_settings(settings: Settings) -> None:
    """Terapkan penimpaan LLM dan prompt dari environment ke lapisan riset.

    Dijalankan sekali saat backend dibangun. Gagal di sini berarti service menolak start: setelan
    yang salah harus terlihat saat deploy, bukan muncul sebagai satu permintaan yang aneh nanti.
    Dan apa yang BENAR-BENAR berlaku ikut dicatat — prompt yang salah versi tetap menghasilkan
    jawaban yang kelihatan masuk akal, jadi satu-satunya cara mengetahuinya adalah dari log.
    """
    from app.prompts import register_db_prompt_source
    from slip_ml import runtime

    register_db_prompt_source(
        database_url=settings.database_url,
        table=settings.prompt_db_table,
        fallback_path=settings.prompt_path,
        fallback=settings.prompt_db_fallback_to_file,
    )
    report = runtime.configure(
        llm={
            "backend": settings.llm_backend,
            "endpoint": settings.llm_endpoint,
            "api_key_env": settings.llm_api_key_env,
            "timeout_seconds": settings.llm_timeout_seconds,
            "region": settings.llm_region,
            "entra_token_url": settings.llm_entra_token_url,
            "mantle_url": settings.llm_mantle_url,
            "model": settings.llm_model,
            "max_tokens": settings.llm_max_tokens,
            "all_fields": settings.llm_all_fields,
        },
        prompt={
            "source": settings.prompt_source,
            "name": settings.prompt_name,
            "version": settings.prompt_version,
            "path": settings.prompt_path,
        },
    )
    state = report["state"]
    logger.info(
        "structuring: LLM backend=%s model=%s endpoint=%s · prompt %s v%s dari %s (%d karakter)",
        state["llm_backend"],
        state["llm_model"],
        state["llm_endpoint"] or "-",
        state["prompt"].get("name") or "-",
        state["prompt"].get("version") or "-",
        state["prompt"].get("source") or "-",
        state["prompt_chars"],
    )
    if report["llm"]:
        logger.info("structuring: setelan config.yaml ditimpa dari environment: %s", ", ".join(sorted(report["llm"])))


def _build_slip_rules(settings: Settings) -> SlipRulesStructurer:
    _apply_llm_settings(settings)
    if settings.enable_llm:
        logger.info("structuring: arbitrase LLM AKTIF (model %s)", settings.llm_model or "bawaan config.yaml")
    else:
        logger.info(
            # Tanpa argumen, logging tidak meng-unescape %% — baris ini mencetaknya apa adanya.
            "structuring: arbitrase LLM mati (ENABLE_LLM=false) — lapisan aturan saja, "
            "terukur 81,5% field benar lawan 89,9% dengan arbitrase"
        )
    return SlipRulesStructurer(use_llm=settings.enable_llm, model=settings.llm_model)


# STRUCTURING_BACKEND -> cara membangunnya. Tambahkan backend di sini dan, kalau butuh setelan, di config.py.
STRUCTURER_BACKENDS: dict[str, Factory[Structurer]] = {
    "slip_rules": _build_slip_rules,
    "mock": lambda settings: MockStructurer(),
}


# --- ML ---------------------------------------------------------------------------


@lru_cache
def get_structurer() -> Structurer:
    settings: Settings = get_settings()
    return build_backend(STRUCTURER_BACKENDS, settings.structuring_backend, settings, "structuring")


# --- pipeline -----------------------------------------------------------------------


SCORING_JOBS_PATH = "/v1/scoring/jobs"


def _next_stage(path: str, name: str) -> NextStageClient:
    settings = get_settings()
    return build_next_stage_client(
        settings,
        base_url=settings.scoring_service_url,
        api_key=settings.scoring_api_key,
        timeout=settings.scoring_timeout_seconds,
        path=path,
        name=name,
    )


@lru_cache
def get_next_stage() -> NextStageClient:
    return _next_stage(SCORING_JOBS_PATH, "scoring service")


@lru_cache
def get_pipeline() -> StagePipeline:
    return build_stage_pipeline(
        get_settings(), stage=STAGE_STRUCTURING, table_prefix=DB_TABLE_PREFIX, next_stage=get_next_stage()
    )


@lru_cache
def get_relay() -> OutboxRelay | None:
    return build_outbox_relay(get_settings(), get_pipeline())


@lru_cache
def get_results() -> StageResults | None:
    return build_stage_results(get_settings())


# The testing endpoints (TESTING_ENDPOINTS): the same pipeline on the testing_* tables, handing off to
# scoring's `-test` endpoint, without callbacks (see ocr_common.testing_endpoints).


@lru_cache
def get_testing_next_stage() -> NextStageClient:
    return _next_stage(testing_path(SCORING_JOBS_PATH), "scoring service (testing)")


@lru_cache
def get_testing_pipeline() -> StagePipeline:
    return build_stage_pipeline(
        get_settings(),
        stage=STAGE_STRUCTURING,
        table_prefix=DB_TABLE_PREFIX,
        next_stage=get_testing_next_stage(),
        testing=True,
    )


@lru_cache
def get_testing_relay() -> OutboxRelay | None:
    return build_outbox_relay(get_settings(), get_testing_pipeline())


@lru_cache
def get_testing_results() -> StageResults | None:
    return build_stage_results(get_settings(), testing=True)


# --- services (cheap to build: one per request) ------------------------------------------


def get_structuring_service() -> StructuringService:
    return StructuringService(get_structurer())


def get_job_service() -> StructuringJobService:
    return StructuringJobService(
        get_pipeline(),
        get_structuring_service(),
        results=get_results(),
        handoff_by_reference=get_settings().pipeline_handoff_by_reference,
    )


def get_testing_job_service() -> StructuringJobService:
    return StructuringJobService(
        get_testing_pipeline(),
        get_structuring_service(),
        results=get_testing_results(),
        handoff_by_reference=get_settings().pipeline_handoff_by_reference,
    )


@lru_cache
def get_reaper() -> StaleJobReaper | None:
    return build_stale_job_reaper(get_settings(), get_pipeline(), get_job_service().resume)


@lru_cache
def get_testing_reaper() -> StaleJobReaper | None:
    return build_stale_job_reaper(get_settings(), get_testing_pipeline(), get_testing_job_service().resume)
