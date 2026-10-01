from typing import cast

from fastapi import APIRouter, Depends, Request

from ocr_common.types import OcrPage
from ocr_common.web.envelope import envelope
from ocr_common.web.request_id import get_request_id
from ocr_common.web.schemas import REQUEST_ID_EXAMPLE, UNAUTHORIZED, error, success_examples
from ocr_common.web.security import verify_api_key

from app.api.schemas import StructureRequest, StructureResponse
from app.dependencies import get_structuring_service
from app.services.structuring_service import StructuringService

router = APIRouter(tags=["Structuring"], dependencies=[Depends(verify_api_key)])

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
    "/v1/structuring/structure",
    response_model=StructureResponse,
    operation_id="structurePages",
    summary="Ubah teks OCR menjadi field slip gaji, sinkron (tanpa job, tanpa callback)",
    description=(
        "Memetakan teks OCR per halaman menjadi **20 field slip gaji**, satu himpunan field per halaman.\n\n"
        "**Satu halaman = satu slip.** Berkas yang memuat tiga bulan menghasilkan tiga slip, masing-masing "
        "dengan totalnya sendiri.\n\n"
        "Dua lapis, sama dengan pipeline penelitian: aturan (sinonim label, pembacaan kolom, turunan aritmetika "
        "gaji bersih = total pendapatan − total potongan) dan, bila `ENABLE_LLM` menyala, arbitrase nilai uang "
        "lewat Bedrock. Terukur pada 50 dokumen berlabel: aturan saja 81,5% field benar, dengan arbitrase 89,9%.\n\n"
        "`source`, `checks`, `counts` dan `llm.compare` ikut dikembalikan bukan sebagai hiasan: tahap scoring "
        "membacanya sebagai fitur model keyakinan."
    ),
    responses={
        200: success_examples(
            "Field yang ditemukan",
            tiga_slip=(
                "Satu berkas berisi tiga bulan",
                envelope(200, "Success", STRUCTURED_EXAMPLE, REQUEST_ID_EXAMPLE),
            ),
        ),
        400: error(400, "Tidak ada teks yang bisa distrukturkan", "tidak ada teks OCR yang bisa distrukturkan"),
        401: UNAUTHORIZED,
        422: error(422, "Validation Error", "body.pages: Field required", errors="VALIDATION_ERROR"),
    },
)
async def structure(
    request: Request,
    body: StructureRequest,
    service: StructuringService = Depends(get_structuring_service),
):
    pages = [cast(OcrPage, page.model_dump()) for page in body.pages]
    data = service.structure(pages)
    return envelope(200, "Success", data, get_request_id(request))
