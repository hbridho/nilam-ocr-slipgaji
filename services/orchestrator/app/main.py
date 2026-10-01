from contextlib import asynccontextmanager

from fastapi import FastAPI

from ocr_common.web.app import create_app

from app.api import extract_ocr, testing
from app.config import get_settings
from app.dependencies import (
    get_extraction_client,
    get_stage_status_clients,
    get_testing_extraction_client,
    get_testing_stage_status_clients,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Klien dibangun pada permintaan pertama yang membutuhkannya; tutup hanya yang benar-benar ada.
    for get_client in (get_extraction_client, get_testing_extraction_client):
        if get_client.cache_info().currsize:
            await get_client().aclose()
            get_client.cache_clear()
    for get_stages in (get_stage_status_clients, get_testing_stage_status_clients):
        if get_stages.cache_info().currsize:
            for stage in get_stages():
                await stage.aclose()
            get_stages.cache_clear()


app = create_app(
    settings=settings,
    title="OCR Slip Gaji Orchestrator API",
    service_name="orchestrator",
    description=(
        "Pintu masuk pipeline OCR Slip Gaji (`ms-bribrain-nilam-ocr-slipgaji-orchestrator`), dan satu-satunya "
        "service yang dipanggil Orkestrasi pusat. Kontraknya mengikuti API spec NILAM [07].\n\n"
        "`POST /v1/extract-ocr` memeriksa berkasnya, menyerahkannya ke tahap OCR, dan menunggu sampai service "
        "terakhir `pipeline_name_sequence` selama `PIPELINE_WAIT_SECONDS`: 200 dengan hasilnya, atau 202 selama "
        "masih berjalan. `GET /v1/extract-ocr/{request_id}` menjawab kontrak yang sama kapan pun sesudahnya.\n\n"
        "**Satu dokumen bisa berisi beberapa slip**: `data` memuat `total_slip` dan array `slip`, satu entri "
        "per halaman.\n\n"
        "**Guardrail dijalankan tahap OCR, bukan di sini**: ketiga service guardrail (blank, blur, identity) "
        "membaca teks OCR, jadi penilaiannya baru mungkin setelah dokumen dibaca. Penolakannya sampai ke klien "
        "sebagai 400 dengan `guardrails: 1` dan `pipeline_last_stage: guardrails`.\n\n"
        "Service ini tidak menyimpan apa pun: job disimpan tiap tahap, dan tahap itu pula yang mengirim "
        "callback hasil. Semua endpoint kecuali /health, /ready dan /metrics memerlukan header X-API-Key."
    ),
    tags=[
        {"name": "Extract OCR", "description": "Mulai pipeline untuk sebuah dokumen, dan baca posisi permintaan"},
    ],
    routers=[extract_ocr.router, *(testing.routers if settings.testing_endpoints else [])],
    readiness={},
    lifespan=lifespan,
    entrypoint=True,
)
