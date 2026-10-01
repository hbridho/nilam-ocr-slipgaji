from contextlib import asynccontextmanager

from fastapi import FastAPI

from ocr_common.web.app import create_app

from app.api import checks
from app.config import get_settings
from app.dependencies import get_identity_check, get_reject_threshold

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_identity_check()  # muat model saat start, bukan saat permintaan pertama datang
    threshold = get_reject_threshold()
    yield
    await threshold.aclose()


app = create_app(
    settings=settings,
    title="OCR Slip Gaji Guardrail Identity API",
    service_name="guardrail-identity",
    description=(
        "Guardrail 3 dari 3 pipeline OCR Slip Gaji, internal: **apakah berkas ini slip gaji, atau "
        "dokumen lain?**\n\n"
        "Ketiga guardrail berdiri sebagai service sendiri dan dipanggil orchestrator PARALEL setelah "
        "tahap OCR: `guardrail-blank` (:8035), `guardrail-blur` (:8036), `guardrail-identity` "
        "(:8037). Masing-masing menjawab satu pertanyaan dan tidak tahu jawaban dua yang lain; yang "
        "menggabungkan ketiganya menjadi satu putusan dokumen adalah orchestrator, dengan urutan "
        "kosong > buram > identitas.\n\n"
        "Modelnya TF-IDF unigram+bigram atas teks OCR ditambah 8 ciri tata letak baris, dengan "
        "kalibrasi Platt; AUC 0,911 out-of-fold. Pada ambang yang dipakai 97,2% slip asli lolos dan "
        "41 dari 51 dokumen bukan-slip tertahan. Disajikan dari JSON dengan numpy saja.\n\n"
        "Dari ketiga guardrail hanya yang ini yang ambangnya boleh dimiliki Orkestrasi pusat "
        "(`IDENTITY_THRESHOLD_URL`): hanya skor ini yang berarti P(slip gaji). Batas `blank` adalah "
        "panjang teks, dan skala gerbang mutu berlawanan arah.\n\n"
        "Semua endpoint kecuali /health, /ready dan /metrics memerlukan header X-API-Key."
    ),
    tags=[
        {"name": "Guardrail", "description": "Pemeriksaan identitas (internal: dipanggil orchestrator)"},
    ],
    routers=[checks.router],
    backends={"identity": settings.identity_backend},
    readiness={},
    backends_example={"identity": "slip_identity"},
    lifespan=lifespan,
)
