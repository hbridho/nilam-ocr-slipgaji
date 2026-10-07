from contextlib import asynccontextmanager

from fastapi import FastAPI

from ocr_common.pipeline.schemas import StageCallback
from ocr_common.web.app import add_stage_callback_webhook, create_app, database_readiness

from app.api import direct, jobs, testing
from app.config import get_settings
from app.dependencies import (
    get_next_stage,
    get_pipeline,
    get_reaper,
    get_redis,
    get_redis_prompt,
    get_relay,
    get_structurer,
    get_testing_next_stage,
    get_testing_pipeline,
    get_testing_reaper,
    get_testing_relay,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_structurer()
    if settings.database_url:
        from ocr_common.pipeline.database import check_connection

        await check_connection(settings.database_url)
    redis_prompt = get_redis_prompt()
    if redis_prompt is not None:
        # Settle on the key's row now (or fill the key), so the first job does not switch prompt.
        await redis_prompt.refresh()
    pipeline = get_pipeline()
    next_stage = get_next_stage()
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
    await next_stage.aclose()
    if settings.testing_endpoints:
        if testing_reaper is not None:
            await testing_reaper.stop()
        await get_testing_pipeline().aclose(settings.pipeline_drain_timeout_seconds, relay=testing_relay)
        await get_testing_next_stage().aclose()
    redis = get_redis()
    if redis is not None:
        await redis.aclose()  # ty: ignore[unresolved-attribute]
    if settings.database_url:
        from ocr_common.pipeline.database import dispose_engines

        await dispose_engines()


app = create_app(
    settings=settings,
    title="OCR Slip Gaji Structuring API",
    service_name="structuring",
    description=(
        "Tahap kedua pipeline slip gaji: teks OCR per halaman menjadi **20 field** slip gaji.\n\n"
        "**Satu halaman = satu slip.** Berkas yang memuat tiga bulan menghasilkan tiga slip, masing-masing "
        "dengan totalnya sendiri; menggabungkannya akan mencampur komponen satu bulan ke total bulan lain.\n\n"
        "Dua lapis, sama dengan pipeline penelitian: aturan (sinonim label, pembacaan kolom, turunan "
        "aritmetika gaji bersih = total pendapatan − total potongan) dan, bila `ENABLE_LLM` menyala, arbitrase "
        "nilai uang lewat Bedrock. Terukur pada 50 dokumen berlabel: aturan saja 81,5% field benar, dengan "
        "arbitrase 89,9%. LLM mati secara default supaya service bisa berjalan tanpa kredensial AWS.\n\n"
        "**Pipeline asinkron:** service OCR POST /v1/structuring/jobs dan menerima 202; service ini bekerja di "
        "latar, menyimpan hasilnya, mengirim callback ke orchestrator, lalu menyerahkan job ke service scoring. "
        "/v1/structuring-direct menjalankan aturan yang sama secara sinkron atas hasil OCR, untuk menguji tahap ini "
        "saja (QC); tidak ada yang dicatat. Semua endpoint kecuali /health "
        "memerlukan header X-API-Key."
    ),
    tags=[
        {"name": "Pipeline", "description": "Tahap pipeline asinkron: 202, kerja di latar, callback, penyerahan"},
        {"name": "Callbacks", "description": "Permintaan yang DIKIRIM service ini ke orchestrator (lihat Webhooks)"},
        {
            "name": "Direct",
            "description": "Tahap ini saja atas keluaran tahap sebelumnya, sinkron: tidak ada yang dicatat (QC)",
        },
    ],
    routers=[jobs.router, direct.router, *([testing.router] if settings.testing_endpoints else [])],
    backends={
        "structuring": settings.structuring_backend,
        "storage": "postgres" if settings.database_url else "memory",
        **({"prompt_cache": "redis"} if settings.structuring_prompt_redis_enabled else {}),
    },
    readiness=database_readiness(settings.database_url),
    backends_example={"structuring": "slip_rules", "storage": "postgres"},
    readiness_example={"database": "ok"},
    lifespan=lifespan,
)

add_stage_callback_webhook(
    app,
    body_model=StageCallback,
    sent=(
        "Once per job of `POST /v1/structuring/jobs`: `stage: STRUCTURING` with `status: DONE` once the fields "
        "are stored, or `status: FAILED` with `error_message` when the document has no "
        "text (the chain stops there). Additionally `stage: SCORING`, `status: FAILED` when structuring succeeded "
        "but the scoring service could not be reached after retries. Without `PIPELINE_OUTBOX` the `STRUCTURING` "
        "callback is sent before the hand-off to scoring; with it the hand-off goes first, so the `SCORING` "
        "callback may arrive before this one."
    ),
)
