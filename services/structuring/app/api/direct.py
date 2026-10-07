from fastapi import APIRouter, Depends, Request

from ocr_common.errors import BadRequest
from ocr_common.slip_gaji import DOCUMENT_TYPE
from ocr_common.web.envelope import envelope
from ocr_common.web.request_id import get_request_id
from ocr_common.web.schemas import REQUEST_ID_EXAMPLE, UNAUTHORIZED, error, success_examples
from ocr_common.web.security import verify_api_key

from app.api.schemas import StructuringDirectRequest, StructuringDirectResponse
from app.dependencies import get_structuring_service
from app.services.structuring_service import StructuringService

router = APIRouter(tags=["Direct"], dependencies=[Depends(verify_api_key)])

_SLIP = {
    "slip_no": 1,
    "page": 1,
    "fields": {
        "nama_perusahaan": "PT SUMBER REJEKI MAKMUR",
        "periode": "2025-02",
        "nama_karyawan": "ANDI SAPUTRA",
        "nomor_induk_karyawan": "202309001",
        "jabatan": "Staff Gudang",
        "divisi": None,
        "status_pegawai": "Tetap",
        "gaji_pokok": 4500000,
        "tunjangan_jabatan": 500000,
        "tunjangan_transport": 300000,
        "tunjangan_makan": 600000,
        "tunjangan_komunikasi": None,
        "tunjangan_lain": None,
        "bonus": None,
        "insentif": None,
        "lembur": 180000,
        "thr": None,
        "total_pendapatan": 6080000,
        "total_potongan": 225000,
        "gaji_bersih": 5855000,
    },
    "source": {
        "gaji_pokok": {"how": "regex", "label": "gaji pokok", "line_no": 9, "line": "Gaji Pokok  Rp 4.500.000"},
        "gaji_bersih": {"how": "derived", "label": None, "line_no": None, "line": None},
    },
    "checks": {"earnings": ["ok"], "netpay": ["ok"]},
    "counts": {"filled": 13, "by_regex": 12, "by_derived": 1, "by_llm": 0, "chars": 612},
    "llm": {"used": False, "asked": False, "error": None, "compare": {}, "disagree": []},
    "missing_mandatory_fields": [],
}
STRUCTURED_EXAMPLE = {
    "document_type": "slip_gaji",
    "n_slips": 3,
    "slips": [_SLIP, {**_SLIP, "slip_no": 2, "page": 2}, {**_SLIP, "slip_no": 3, "page": 3}],
    "llm_used": False,
    "elapsed_ms": 227.5,
    "reject_reason": None,
}


@router.post(
    "/v1/structuring-direct",
    response_model=StructuringDirectResponse,
    operation_id="structureDirect",
    summary="Strukturkan hasil OCR sekarang, sinkron (tanpa job, tanpa callback, tanpa penyerahan)",
    description=(
        "**Untuk menguji satu tahap saja (QC).** Menerima keluaran tahap sebelumnya, hasil OCR service "
        "extraction (`data` dari `POST /v1/extraction/extract`, atau `result` job-nya), menjalankan aturan "
        "structuring yang sama dengan pipeline, dan menjawab slip gaji terstruktur di badan respons. Tidak ada "
        "yang dicatat: tidak ada baris `nilam_structuring_jobs`, callback, maupun penyerahan ke scoring.\n\n"
        "Jawabannya persis `result` pada `GET /v1/structuring/jobs/{request_id}` dan `structuring` yang diterima "
        "tahap scoring: tempel ke `POST /v1/scoring-direct` untuk menguji tahap berikutnya.\n\n"
        "**Satu halaman = satu slip.** Dua lapis: aturan (sinonim label, pembacaan kolom, turunan aritmetika) dan, "
        "bila `ENABLE_LLM` menyala, arbitrase nilai uang lewat LLM. Hasil OCR tanpa satu pun halaman berteks: 400."
    ),
    responses={
        200: success_examples(
            "Hasil OCR sudah distrukturkan",
            tiga_slip=(
                "Satu berkas berisi tiga bulan",
                envelope(200, "Success", STRUCTURED_EXAMPLE, REQUEST_ID_EXAMPLE),
            ),
        ),
        400: error(
            400,
            "Tidak ada teks yang bisa distrukturkan, atau `document_type` tidak didukung",
            "tidak ada teks OCR yang bisa distrukturkan",
        ),
        401: UNAUTHORIZED,
        422: error(422, "Validation Error", "body.ocr: Field required", errors="VALIDATION_ERROR"),
    },
)
async def structure_direct(
    request: Request,
    body: StructuringDirectRequest,
    service: StructuringService = Depends(get_structuring_service),
):
    if body.document_type != DOCUMENT_TYPE:
        raise BadRequest(f"Unsupported document_type: {body.document_type}. Supported: ['{DOCUMENT_TYPE}']")
    pages = StructuringService.pages_from_ocr(body.ocr.model_dump(exclude_unset=True))
    data = await service.run(pages)
    return envelope(200, "Success", data, body.request_id or get_request_id(request))
