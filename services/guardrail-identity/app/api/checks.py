from fastapi import APIRouter, Depends, Request

from ocr_common.web.envelope import envelope
from ocr_common.web.request_id import get_request_id
from ocr_common.web.schemas import UNAUTHORIZED, error, success_examples
from ocr_common.web.security import verify_api_key

from app.api.schemas import RID, IdentityReportResponse, TextCheckRequest
from app.dependencies import get_identity_service
from app.services.identity_service import IdentityService

router = APIRouter(tags=["Guardrail"], dependencies=[Depends(verify_api_key)])

_ACCEPTED = {
    "check": "identity",
    "verdict": "slip_gaji",
    "passed": True,
    "reason": None,
    "proba_slip_gaji": 0.9979,
    "confidence": 0.9979,
    "reject_threshold": 0.47,
    "model": "slip_identity",
}
_WRONG = {
    "check": "identity",
    "verdict": "bukan_slip_gaji",
    "passed": False,
    "reason": "Dokumen ini bukan slip gaji. Mohon unggah slip gaji.",
    "proba_slip_gaji": 0.1329,
    "confidence": 0.8671,
    "reject_threshold": 0.47,
    "model": "slip_identity",
}

_WHY = (
    "**Satu pemeriksaan dari tiga, dan yang paling dalam.** TF-IDF unigram+bigram atas teks OCR "
    "ditambah 8 ciri tata letak baris, dengan kalibrasi Platt. AUC 0,911 out-of-fold atas dokumen "
    "berlabel; pada ambang yang dipakai 97,2% slip asli lolos dan 41 dari 51 dokumen bukan-slip "
    "tertahan.\n\n"
    "**Kenapa teks, bukan gambar.** Diukur pada korpus yang sama, model piksel hanya mencapai AUC "
    "0,638 — pada titik operasi yang sama ia menahan 12 dari 51 dokumen bukan-slip, sementara model "
    "teks menahan 41. Karena teks OCR baru ada setelah tahap OCR, guardrail slip gaji berjalan "
    "SETELAH OCR dan sebelum structuring, bukan sebelum pipeline seperti guardrail berbasis "
    "gambar.\n\n"
    "**Kenapa terpisah dari mutu.** Keterbacaan dan identitas dua pertanyaan berbeda. Menyatukannya "
    "jadi satu model empat kelas menjatuhkan precision bin 2 di sini dari 90% ke 55-70% dan "
    "memendekkan band murni dari 39 ke 18-22 dokumen.\n\n"
    "**Ambangnya boleh dari luar.** Dari ketiga guardrail hanya yang ini yang titik operasinya "
    "wajar digeser Orkestrasi pusat (`IDENTITY_THRESHOLD_URL`), karena hanya skor ini yang berarti "
    "P(slip gaji). `reject_threshold` di laporan selalu ambang yang benar-benar dipakai.\n\n"
    "**Pada teks yang hancur, jawaban di sini tebakan.** Ia tetap dijawab karena ketiga guardrail "
    "dipanggil PARALEL; yang membuangnya adalah orchestrator saat menggabungkan ketiganya "
    "(`slip_ml.guard.combine`), yang mendahulukan `blank` lalu `blur` di atas pemeriksaan ini."
)


@router.post(
    "/v1/guardrail/identity/check",
    response_model=IdentityReportResponse,
    operation_id="checkIdentity",
    summary="Guardrail 3 · Ini slip gaji? (internal: dipanggil orchestrator, paralel)",
    description=(
        "Menilai identitas dokumen dari teks OCR seluruh halaman dan menjawab satu hal saja, "
        "**tanpa** memulai apa pun: tidak ada job, tidak ada callback.\n\n"
        + _WHY
        + "\n\nSelalu 200 ketika permintaannya sah — baca `data.verdict` dan `data.passed`."
    ),
    responses={
        200: success_examples(
            "Dokumen dinilai",
            slip_gaji=("Lolos", envelope(200, "OK", _ACCEPTED, RID)),
            bukan_slip_gaji=("Dokumen salah", envelope(200, "OK", _WRONG, RID)),
        ),
        401: UNAUTHORIZED,
        422: error(422, "Validation Error", "body.text: Field required", errors="VALIDATION_ERROR"),
        500: error(500, "Model identitas gagal", "identity model returned an unexpected response"),
    },
)
async def check_identity(
    request: Request,
    body: TextCheckRequest,
    service: IdentityService = Depends(get_identity_service),
):
    report = await service.check(body.text)
    return envelope(200, "OK", report, body.request_id or get_request_id(request))
