from fastapi import APIRouter, Depends, Request

from ocr_common.web.envelope import envelope
from ocr_common.web.request_id import get_request_id
from ocr_common.web.schemas import UNAUTHORIZED, error, success_examples
from ocr_common.web.security import verify_api_key

from app.api.schemas import RID, BlankReportResponse, TextCheckRequest
from app.dependencies import get_blank_service
from app.services.blank_service import BlankService

router = APIRouter(tags=["Guardrail"], dependencies=[Depends(verify_api_key)])

_OK = {
    "check": "blank",
    "verdict": "ok",
    "passed": True,
    "reason": None,
    "chars": 1789,
    "max_chars": 20,
    "model": "slip_blank",
}
_BLANK = {
    "check": "blank",
    "verdict": "blank",
    "passed": False,
    "reason": "Dokumen kosong: tidak ada teks yang terbaca. Pastikan halaman yang diunggah benar.",
    "chars": 0,
    "max_chars": 20,
    "model": "slip_blank",
}

_WHY = (
    "**Satu pemeriksaan dari tiga, dan yang paling dangkal dengan sengaja.** Ia hanya menghitung "
    "panjang teks OCR: `chars <= max_chars` berarti kosong. Tidak ada bobot, tidak ada model, tidak "
    "ada ambang yang bisa bergeser.\n\n"
    "**Kenapa aturan, bukan model.** Tidak ada yang perlu ditaksir di sini, dan model yang dilatih "
    "atas halaman kosong buatan akan terlihat sempurna di validasi sambil salah pada halaman kosong "
    "yang bentuknya lain.\n\n"
    "**Kenapa ia yang pertama dalam urutan putusan.** Tanpa teks tidak ada mutu maupun identitas "
    "yang bisa dinilai. Karena ketiga guardrail dipanggil PARALEL, dua pemeriksaan lain tetap "
    "menjawab pada halaman kosong — dan jawaban mereka di situ tidak berarti apa-apa. Yang "
    "mendahulukan `blank` adalah orchestrator saat menggabungkan ketiganya "
    "(`slip_ml.guard.combine`), bukan service ini."
)


@router.post(
    "/v1/guardrail/blank/check",
    response_model=BlankReportResponse,
    operation_id="checkBlank",
    summary="Guardrail 1 · Halaman ini kosong? (internal: dipanggil orchestrator, paralel)",
    description=(
        "Menilai panjang teks OCR seluruh halaman dan menjawab satu hal saja, **tanpa** memulai apa "
        "pun: tidak ada job, tidak ada callback.\n\n"
        + _WHY
        + "\n\nSelalu 200 ketika permintaannya sah — baca `data.verdict` dan `data.passed`. Teks "
        "kosong juga 200, dengan `verdict: blank`: itu jawaban, bukan galat."
    ),
    responses={
        200: success_examples(
            "Halaman dinilai",
            ok=("Ada teks", envelope(200, "OK", _OK, RID)),
            blank=("Kosong", envelope(200, "OK", _BLANK, RID)),
        ),
        401: UNAUTHORIZED,
        422: error(422, "Validation Error", "body.text: Field required", errors="VALIDATION_ERROR"),
    },
)
async def check_blank(
    request: Request,
    body: TextCheckRequest,
    service: BlankService = Depends(get_blank_service),
):
    report = await service.check(body.text)
    return envelope(200, "OK", report, body.request_id or get_request_id(request))
