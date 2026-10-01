from fastapi import APIRouter, Depends, Request

from ocr_common.web.envelope import envelope
from ocr_common.web.request_id import get_request_id
from ocr_common.web.schemas import REQUEST_ID_EXAMPLE, UNAUTHORIZED, error, success_examples
from ocr_common.web.security import verify_api_key

from app.api.schemas import ScoreRequest, ScoreResponse
from app.dependencies import get_confidence_service
from app.services.confidence_service import ConfidenceService

router = APIRouter(tags=["Scoring"], dependencies=[Depends(verify_api_key)])

SCORING_EXAMPLE = {
    "slips": [
        {
            "slip_no": 1,
            "scores": {
                "nama_karyawan": 76.71,
                "jabatan": 76.71,
                "gaji_pokok": 93.6,
                "tunjangan_makan": 88.89,
                "total_pendapatan": 92.47,
                "gaji_bersih": 92.47,
            },
        }
    ],
    "threshold": 80.0,
    "model": "conf_v2",
}


@router.post(
    "/v1/scoring/score",
    response_model=ScoreResponse,
    operation_id="scoreSlips",
    summary="Skor keyakinan per field, sinkron (tanpa job, tanpa callback)",
    description=(
        "Menghitung **P(nilai field ini benar)** untuk setiap nilai yang diextraction, sebagai skor 0-1 yang "
        "sudah dikalibrasi (isotonic), lalu dikembalikan apa adanya.\n\n"
        "Modelnya regresi logistik atas 9 variabel + one-hot field: kesepakatan regex vs LLM, jarak nominal ke "
        "median field, seberapa sering field ini benar-benar ada, jumlah digit, hasil cek aritmetika, nilai nol, "
        "dan porsi kotak OCR berskor rendah di halaman. Dilatih pada 1.428 nilai dari 50 dokumen berlabel; "
        "out-of-fold (GroupKFold per dokumen): AUC 0,826 · Gini 0,652 · ECE 0,037.\n\n"
        "**Tidak ada keputusan di sini.** Pemetaan ke `confidence` 0/1 terjadi di kontrak `extract-ocr` memakai "
        "`threshold`, supaya ambangnya bisa diubah tanpa melatih ulang apa pun. Pada ambang 80: precision 93,4%, "
        "recall 75,6%.\n\n"
        "Field tanpa nilai tidak diberi skor: tidak ada yang perlu dinilai di sana, dan memberinya 0 akan "
        "tercampur dengan nilai yang benar-benar diragukan."
    ),
    responses={
        200: success_examples(
            "Skor untuk tiap slip",
            satu_slip=("Satu slip", envelope(200, "Success", SCORING_EXAMPLE, REQUEST_ID_EXAMPLE)),
        ),
        400: error(400, "Tidak ada slip untuk dinilai", "tidak ada slip untuk dinilai"),
        401: UNAUTHORIZED,
        422: error(422, "Validation Error", "body.slips: Field required", errors="VALIDATION_ERROR"),
        500: error(500, "Model keyakinan gagal", "Internal error in SCORING stage"),
    },
)
async def score(
    request: Request,
    body: ScoreRequest,
    service: ConfidenceService = Depends(get_confidence_service),
):
    data = service.score({"slips": [slip.model_dump() for slip in body.slips]})
    return envelope(200, "Success", data, get_request_id(request))
