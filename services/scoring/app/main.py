from contextlib import asynccontextmanager

from fastapi import FastAPI

from ocr_common.pipeline.schemas import ScoringStageCallback
from ocr_common.web.app import add_stage_callback_webhook, create_app, database_readiness

from app.api import direct, jobs, testing
from app.config import get_settings
from app.dependencies import (
    get_confidence_model,
    get_pipeline,
    get_reaper,
    get_relay,
    get_testing_pipeline,
    get_testing_reaper,
    get_testing_relay,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_confidence_model()  # muat model saat start, bukan saat job pertama datang
    if settings.database_url:
        from ocr_common.pipeline.database import check_connection

        await check_connection(settings.database_url)
    pipeline = get_pipeline()
    relay = get_relay()
    if relay is not None:
        relay.start()
    reaper = get_reaper()
    if reaper is not None:
        reaper.start()
    testing_relay = testing_reaper = None
    if settings.testing_endpoints:
        testing_relay, testing_reaper = get_testing_relay(), get_testing_reaper()
        if testing_relay is not None:
            testing_relay.start()
        if testing_reaper is not None:
            testing_reaper.start()
    yield
    if reaper is not None:
        await reaper.stop()
    await pipeline.aclose(settings.pipeline_drain_timeout_seconds, relay=relay)
    if settings.testing_endpoints:
        if testing_reaper is not None:
            await testing_reaper.stop()
        await get_testing_pipeline().aclose(settings.pipeline_drain_timeout_seconds, relay=testing_relay)
    if settings.database_url:
        from ocr_common.pipeline.database import dispose_engines

        await dispose_engines()


app = create_app(
    settings=settings,
    title="OCR Slip Gaji Scoring API",
    service_name="scoring",
    description=(
        "Tahap terakhir pipeline slip gaji: memberi **skor keyakinan per field** — P(nilai ini benar), 0-1, "
        "terkalibrasi isotonic.\n\n"
        "Modelnya regresi logistik 9 variabel + one-hot field, dilatih pada 1.428 nilai dari 50 dokumen "
        "berlabel; out-of-fold AUC 0,826 · Gini 0,652 · ECE 0,037. Pada ambang 80: precision 93,4%, "
        "recall 75,6%.\n\n"
        "**Tidak ada keputusan di sini.** Skor mentah yang dikembalikan; pemetaan ke `confidence` 0/1 terjadi "
        "di kontrak `extract-ocr`, supaya ambangnya bisa diubah tanpa melatih ulang apa pun.\n\n"
        "**Pipeline asinkron:** service structuring POST /v1/scoring/jobs dan menerima 202; service ini "
        "memberi skor di latar, menyimpan hasilnya, dan mengirim callback berisi hasil akhir ke orchestrator. "
        "/v1/scoring-direct menjalankan model yang sama secara sinkron atas hasil structuring, untuk menguji "
        "tahap ini saja (QC); tidak ada yang dicatat. Semua endpoint kecuali /health "
        "memerlukan header X-API-Key."
    ),
    tags=[
        {"name": "Pipeline", "description": "Tahap pipeline asinkron: 202, kerja di latar, callback"},
        {"name": "Callbacks", "description": "Permintaan yang DIKIRIM service ini ke orchestrator (lihat Webhooks)"},
        {
            "name": "Direct",
            "description": "Tahap ini saja atas keluaran tahap sebelumnya, sinkron: tidak ada yang dicatat (QC)",
        },
    ],
    routers=[jobs.router, direct.router, *([testing.router] if settings.testing_endpoints else [])],
    backends={
        "scoring": settings.scoring_backend,
        "storage": "postgres" if settings.database_url else "memory",
    },
    readiness=database_readiness(settings.database_url),
    backends_example={"scoring": "conf_v2", "storage": "postgres"},
    readiness_example={"database": "ok"},
    lifespan=lifespan,
)

add_stage_callback_webhook(
    app,
    body_model=ScoringStageCallback,
    sent=(
        "Once per job of `POST /v1/scoring/jobs`, the last callback of a request: `stage: SCORING` with "
        "`status: DONE` and `result` = the **final result** (fields, per-field confidences, and the guardrails "
        "result that was submitted), or `status: FAILED` with `error_message` and `result: null`."
    ),
)
