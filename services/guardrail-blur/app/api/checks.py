from fastapi import APIRouter, Depends, Request

from ocr_common.web.envelope import envelope
from ocr_common.web.request_id import get_request_id
from ocr_common.web.schemas import UNAUTHORIZED, error, success_examples
from ocr_common.web.security import verify_api_key

from app.api.schemas import RID, BlurReportResponse, TextCheckRequest
from app.dependencies import get_blur_service
from app.services.blur_service import BlurService

router = APIRouter(tags=["Guardrail"], dependencies=[Depends(verify_api_key)])

_OK = {
    "check": "blur",
    "verdict": "ok",
    "passed": True,
    "reason": None,
    "p_broken": 0.0153,
    "threshold": 0.9584,
    "chars": 1789,
    "ocr_mean": 0.9812,
    "blank": False,
    "model": "slip_blur",
}
_BLUR = {
    "check": "blur",
    "verdict": "blur",
    "passed": False,
    "reason": "Dokumen terlalu buram untuk dibaca. Mohon foto ulang dengan lebih jelas.",
    "p_broken": 0.9997,
    "threshold": 0.9584,
    "chars": 214,
    "ocr_mean": 0.58,
    "blank": False,
    "model": "slip_blur",
}

_WHY = (
    "**Satu pemeriksaan dari tiga.** Regresi logistik atas 6 ciri mutu OCR — skor rata-rata, skor "
    "minimum, porsi kotak berskor < 0,90, jumlah kotak, jumlah karakter, karakter per kotak. AUC "
    "0,985 out-of-fold.\n\n"
    "**Tidak ada satu kata pun yang dibaca, dan itu bukan kelalaian.** Gerbang ini tidak boleh "
    "peduli dokumennya berbunyi apa, supaya ia tetap bekerja pada dokumen yang belum pernah "
    "dilihatnya. Menambahkan TF-IDF kata ke gerbang ini hanya menaikkan AUC 0,989 menjadi 0,992 — "
    "tidak sebanding dengan kehilangan sifat itu.\n\n"
    "**Kenapa terpisah dari identitas.** Keterbacaan dan identitas dua pertanyaan berbeda. Diukur "
    "pada korpus yang sama, menyatukannya jadi satu model empat kelas menjatuhkan precision bin 2 "
    "identitas dari 90% ke 55-70% dan memendekkan band murni dari 39 ke 18-22 dokumen.\n\n"
    "**`confidence` adalah masukan utamanya.** Tanpa ringkasan skor OCR gerbang hanya melihat "
    "panjang teks, dan dokumen buram kerap menghasilkan banyak karakter yang semuanya salah — "
    "panjang saja tidak membedakannya dari halaman yang terbaca baik."
)


@router.post(
    "/v1/guardrail/blur/check",
    response_model=BlurReportResponse,
    operation_id="checkBlur",
    summary="Guardrail 2 · Halaman ini masih terbaca? (internal: dipanggil orchestrator, paralel)",
    description=(
        "Menilai mutu teks OCR seluruh halaman dan menjawab satu hal saja, **tanpa** memulai apa "
        "pun: tidak ada job, tidak ada callback.\n\n"
        + _WHY
        + "\n\nSelalu 200 ketika permintaannya sah — baca `data.verdict` dan `data.passed`. "
        "Perhatikan `data.blank`: pada halaman tanpa teks model ini menjawab `blur`, dan yang "
        "mendahulukan `blank` adalah orchestrator saat menggabungkan ketiga guardrail "
        "(`slip_ml.guard.combine`), bukan service ini."
    ),
    responses={
        200: success_examples(
            "Halaman dinilai",
            ok=("Masih terbaca", envelope(200, "OK", _OK, RID)),
            blur=("Terlalu buram", envelope(200, "OK", _BLUR, RID)),
        ),
        401: UNAUTHORIZED,
        422: error(422, "Validation Error", "body.text: Field required", errors="VALIDATION_ERROR"),
        500: error(500, "Model gerbang mutu gagal", "blur model returned an unexpected response"),
    },
)
async def check_blur(
    request: Request,
    body: TextCheckRequest,
    service: BlurService = Depends(get_blur_service),
):
    report = await service.check(body.text, body.confidence)
    return envelope(200, "OK", report, body.request_id or get_request_id(request))
