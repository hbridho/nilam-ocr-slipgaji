from fastapi import APIRouter, Depends, Form, Request, UploadFile

from ocr_common.image_validation import PAYLOAD_TOO_LARGE_MESSAGE
from ocr_common.web.envelope import envelope
from ocr_common.web.intake import FileField, FileUrlField, read_image
from ocr_common.web.request_id import get_request_id
from ocr_common.web.schemas import REQUEST_ID_EXAMPLE, UNAUTHORIZED, error, success_examples
from ocr_common.web.security import verify_api_key

from app.api.schemas import ExtractResponse
from app.dependencies import get_extraction_service
from app.services.extraction_service import ExtractionService

router = APIRouter(tags=["Extraction"], dependencies=[Depends(verify_api_key)])

OCR_RESULT_EXAMPLE = {
    "engine": "rapidocr",
    "model": "rapidocr",
    "elapsed_ms": 2841.7,
    "n_pages": 3,
    "full_text": "PT SUMBER REJEKI MAKMUR\nSLIP GAJI\nPeriode  : Februari 2025\nGaji Pokok  Rp 4.500.000\n...",
    "pages": [
        {
            "page": 1,
            "text": "PT SUMBER REJEKI MAKMUR\nSLIP GAJI\nPeriode  : Februari 2025\nGaji Pokok  Rp 4.500.000",
            "confidence": {"engine": "rapidocr", "n_boxes": 84, "mean": 0.9812, "min": 0.4123, "n_low": 4},
        }
    ],
    "guardrails": {
        "passed": True,
        "reason": None,
        "verdict": "slip_gaji",
        "rejected_by": None,
        "document": {
            "verdict": "accepted",
            "confidence": 0.9934,
            "n_pages": 3,
            "threshold": 0.47,
        },
        "checks": {
            "blank": {
                "check": "blank",
                "verdict": "ok",
                "passed": True,
                "reason": None,
                "chars": 1789,
                "max_chars": 20,
                "model": "slip_blank",
            },
            "blur": {
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
            },
            "identity": {
                "check": "identity",
                "verdict": "slip_gaji",
                "passed": True,
                "reason": None,
                "proba_slip_gaji": 0.9934,
                "confidence": 0.9934,
                "reject_threshold": 0.47,
                "model": "slip_identity",
            },
        },
        "skipped": [],
        "unavailable": [],
        "pages": [],
    },
    "reject_reason": None,
}


@router.post(
    "/v1/extraction/extract",
    response_model=ExtractResponse,
    operation_id="extractText",
    summary="OCR mentah satu dokumen, sinkron (tanpa job, tanpa callback)",
    description=(
        "Menjalankan mesin OCR dan mengembalikan teks per halaman beserta ringkasan skor keyakinannya, lalu "
        "meminta guardrail menilai teks itu.\n\n"
        "**Halaman tidak digabung.** Satu berkas slip gaji lazim memuat tiga bulan berturut-turut; menggabungkan "
        "teksnya akan mencampur komponen satu bulan ke total bulan lain. `full_text` ada untuk guardrail, "
        "`pages[]` untuk structuring.\n\n"
        "Kirim dokumen sebagai `file` atau `file_url`, tepat salah satu."
    ),
    responses={
        200: success_examples(
            "Teks terbaca",
            slip_gaji=("Slip gaji 3 bulan", envelope(200, "Success", OCR_RESULT_EXAMPLE, REQUEST_ID_EXAMPLE)),
        ),
        400: error(
            400, "Berkas buruk (kosong, jenis tidak didukung, tak terbaca) atau tanpa teks", "Uploaded file is empty"
        ),
        401: UNAUTHORIZED,
        413: error(
            413,
            "Dokumen melebihi `MAX_UPLOAD_BYTES` (2,5 MB bawaan)",
            PAYLOAD_TOO_LARGE_MESSAGE.format(limit="2,5 MB"),
        ),
        422: error(422, "Validation Error", "body.file: Expected UploadFile, received: str", errors="VALIDATION_ERROR"),
        500: error(500, "Mesin OCR gagal", "Internal error in OCR stage"),
        503: error(
            503,
            "Sebuah service guardrail tidak terjangkau dan GUARDRAILS_FAIL_OPEN mati",
            "guardrail-blur service is unavailable",
        ),
    },
)
async def extract(
    request: Request,
    file: UploadFile | str | None = FileField,
    file_url: str | None = FileUrlField,
    guardrails: bool = Form(True, description="`false` melewati ketiga guardrail untuk permintaan ini"),
    service: ExtractionService = Depends(get_extraction_service),
):
    content, filename, content_type = await read_image(request, file, file_url)
    request_id = get_request_id(request)
    data = await service.extract(filename, content_type, content, request_id=request_id, run_guardrails=guardrails)
    return envelope(200, "Success", data, request_id)
