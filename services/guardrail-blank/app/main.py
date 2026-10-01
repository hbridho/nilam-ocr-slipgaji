from contextlib import asynccontextmanager

from fastapi import FastAPI

from ocr_common.web.app import create_app

from app.api import checks
from app.config import get_settings
from app.dependencies import get_blank_check

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_blank_check()  # baca batasnya saat start, bukan saat permintaan pertama datang
    yield


app = create_app(
    settings=settings,
    title="OCR Slip Gaji Guardrail Blank API",
    service_name="guardrail-blank",
    description=(
        "Guardrail 1 dari 3 pipeline OCR Slip Gaji, internal: **apakah halaman ini kosong?**\n\n"
        "Ketiga guardrail berdiri sebagai service sendiri dan dipanggil orchestrator PARALEL setelah "
        "tahap OCR: `guardrail-blank` (:8035), `guardrail-blur` (:8036), `guardrail-identity` "
        "(:8037). Masing-masing menjawab satu pertanyaan dan tidak tahu jawaban dua yang lain; yang "
        "menggabungkan ketiganya menjadi satu putusan dokumen adalah orchestrator, dengan urutan "
        "kosong > buram > identitas.\n\n"
        "Pemeriksaan ini aturan satu baris atas panjang teks — tanpa model, tanpa bobot, tanpa "
        "ambang yang bisa bergeser bersama korpus. Halaman tanpa teks tidak punya mutu maupun "
        "identitas yang bisa dinilai, jadi jawabannya harus datang lebih dulu dan tidak boleh "
        "bergantung pada apa pun yang dipelajari.\n\n"
        "Semua endpoint kecuali /health, /ready dan /metrics memerlukan header X-API-Key."
    ),
    tags=[
        {"name": "Guardrail", "description": "Pemeriksaan kosong (internal: dipanggil orchestrator)"},
    ],
    routers=[checks.router],
    backends={"blank": settings.blank_backend},
    readiness={},
    backends_example={"blank": "slip_blank"},
    lifespan=lifespan,
)
