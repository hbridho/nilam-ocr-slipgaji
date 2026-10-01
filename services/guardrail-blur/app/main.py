from contextlib import asynccontextmanager

from fastapi import FastAPI

from ocr_common.web.app import create_app

from app.api import checks
from app.config import get_settings
from app.dependencies import get_blur_check

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_blur_check()  # muat model saat start, bukan saat permintaan pertama datang
    yield


app = create_app(
    settings=settings,
    title="OCR Slip Gaji Guardrail Blur API",
    service_name="guardrail-blur",
    description=(
        "Guardrail 2 dari 3 pipeline OCR Slip Gaji, internal: **apakah halaman ini masih cukup "
        "terbaca untuk diekstraksi?**\n\n"
        "Ketiga guardrail berdiri sebagai service sendiri dan dipanggil orchestrator PARALEL setelah "
        "tahap OCR: `guardrail-blank` (:8035), `guardrail-blur` (:8036), `guardrail-identity` "
        "(:8037). Masing-masing menjawab satu pertanyaan dan tidak tahu jawaban dua yang lain; yang "
        "menggabungkan ketiganya menjadi satu putusan dokumen adalah orchestrator, dengan urutan "
        "kosong > buram > identitas.\n\n"
        "Modelnya regresi logistik atas 6 ciri mutu OCR (AUC 0,985), disajikan dari JSON dengan "
        "numpy saja — tanpa sklearn, tanpa pickle, tanpa OpenCV. Tidak ada satu kata pun yang "
        "dibaca: gerbang ini sengaja tidak peduli dokumennya berbunyi apa, supaya ia tetap bekerja "
        "pada dokumen yang belum pernah dilihatnya.\n\n"
        "Semua endpoint kecuali /health, /ready dan /metrics memerlukan header X-API-Key."
    ),
    tags=[
        {"name": "Guardrail", "description": "Pemeriksaan mutu (internal: dipanggil orchestrator)"},
    ],
    routers=[checks.router],
    backends={"blur": settings.blur_backend},
    readiness={},
    backends_example={"blur": "slip_blur"},
    lifespan=lifespan,
)
