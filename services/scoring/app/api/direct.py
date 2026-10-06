from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from ocr_common.errors import BadRequest
from ocr_common.slip_gaji import DOCUMENT_TYPE, contract_data, final_result
from ocr_common.web.envelope import envelope
from ocr_common.web.request_id import get_request_id
from ocr_common.web.schemas import REQUEST_ID_EXAMPLE, UNAUTHORIZED, error, success_examples
from ocr_common.web.security import verify_api_key

from app.api.schemas import ScoringDirectRequest, ScoringDirectResponse
from app.config import Settings, get_settings
from app.dependencies import get_confidence_service
from app.services.confidence_service import ConfidenceService

router = APIRouter(tags=["Direct"], dependencies=[Depends(verify_api_key)])

SCORING_EXAMPLE = {
    "slips": [
        {
            "slip_no": 1,
            "scores": {
                "nama_karyawan": 0.7671,
                "jabatan": 0.7671,
                "gaji_pokok": 0.936,
                "tunjangan_makan": 0.8889,
                "total_pendapatan": 0.9247,
                "gaji_bersih": 0.9247,
            },
        }
    ],
    "threshold": 0.5,
    "model": "conf_v2",
    "column_confidence_threshold": None,
}
_DIRECT_EXAMPLE = {
    **SCORING_EXAMPLE,
    "data": {
        "total_slip": 1,
        "slip": [
            {
                "page": 1,
                "gaji_pokok": {"value": 4500000, "confidence": 1},
                "gaji_bersih": {"value": 5855000, "confidence": 1},
                "missing_mandatory_fields": [],
            }
        ],
    },
}


@router.post(
    "/v1/scoring-direct",
    response_model=ScoringDirectResponse,
    operation_id="scoreDirect",
    summary="Beri skor slip terstruktur sekarang, sinkron (tanpa job, tanpa callback)",
    description=(
        "**Untuk menguji satu tahap saja (QC).** Menerima keluaran tahap sebelumnya, hasil structuring (`data` "
        "dari `POST /v1/structuring-direct`, atau `result` job structuring), menjalankan model keyakinan, dan "
        "menjawab di badan respons. Tidak ada yang dicatat: tidak ada baris `nilam_scoring_jobs`, callback, "
        "maupun baris hasil Orkestrasi.\n\n"
        "Jawabannya `result` yang sama seperti `GET /v1/scoring/jobs/{request_id}` (P(nilai benar) 0-1 per field), "
        "ditambah `data`: kontrak `extract-ocr` dengan confidence 0/1 menurut `column_confidence_threshold` "
        "(`all_field` disebar ke semua field; kunci field sendiri menang), lalu `FIELD_CONFIDENCE_THRESHOLD`. "
        "Field tanpa nilai tidak diberi skor."
    ),
    responses={
        200: success_examples(
            "Slip terstruktur sudah dinilai",
            satu_slip=("Satu slip", envelope(200, "Success", _DIRECT_EXAMPLE, REQUEST_ID_EXAMPLE)),
        ),
        400: error(400, "Tidak ada slip, atau `document_type` tidak didukung", "tidak ada slip untuk dinilai"),
        401: UNAUTHORIZED,
        422: error(422, "Validation Error", "body.structuring: Field required", errors="VALIDATION_ERROR"),
        500: error(500, "Model keyakinan gagal", "Internal error in SCORING stage"),
    },
)
async def score_direct(
    request: Request,
    body: ScoringDirectRequest,
    service: ConfidenceService = Depends(get_confidence_service),
    settings: Settings = Depends(get_settings),
):
    if body.document_type != DOCUMENT_TYPE:
        raise BadRequest(f"Unsupported document_type: {body.document_type}. Supported: ['{DOCUMENT_TYPE}']")
    structuring = body.structuring.model_dump()
    guardrails = body.guardrails.model_dump(exclude_unset=True) if body.guardrails is not None else None
    result = await run_in_threadpool(service.score, structuring)
    columns = body.column_confidence_threshold
    final = final_result(DOCUMENT_TYPE, guardrails, structuring, result)
    data = {
        **result,
        "column_confidence_threshold": columns,
        "data": contract_data(final, settings.field_confidence_threshold, columns),
    }
    return envelope(200, "Success", data, body.request_id or get_request_id(request))
