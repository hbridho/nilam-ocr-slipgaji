import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Form, Request, Response, UploadFile

from ocr_common.image_validation import PAYLOAD_TOO_LARGE_MESSAGE
from ocr_common.pipeline import InvalidSequence, validate_sequence
from ocr_common.slip_gaji import DOCUMENT_TYPE
from ocr_common.thresholds import InvalidThreshold, parse_column_thresholds, parse_guardrails_threshold
from ocr_common.web.intake import FileField, FileUrlField, read_image
from ocr_common.web.request_id import adopt_request_id, reset_request_id
from ocr_common.web.schemas import UNAUTHORIZED, error, success_examples
from ocr_common.web.security import verify_api_key

from app.api.extract_contract import (
    COMPLETED_MESSAGE,
    ENTRY,
    PROCESSING_MESSAGE,
    REJECTED_CODE,
    extract_body,
    extract_response,
)
from app.api.schemas import ExtractOcrResponse
from app.config import Settings, get_settings
from app.dependencies import get_extract_service
from app.services.document_checks import TOO_MANY_PAGES_MESSAGE
from app.services.extract_service import ExtractOcrService

router = APIRouter(tags=["Extract OCR"], dependencies=[Depends(verify_api_key)])

RID = "OCR_9cb01af2-493d-446d-b191-af120333f6d0"
DEFAULT_SEQUENCE_EXAMPLE = '["guardrails","extraction","structuring","scoring"]'

_SLIP = {
    "page": 1,
    "nama_perusahaan": {"value": "PT SUMBER REJEKI MAKMUR", "confidence": 1},
    "periode": {"value": "2025-02", "confidence": 1},
    "nama_karyawan": {"value": "ANDI SAPUTRA", "confidence": 1},
    "nomor_induk_karyawan": {"value": "202309001", "confidence": 1},
    "jabatan": {"value": "Staff Gudang", "confidence": 1},
    "divisi": {"value": "Produksi", "confidence": 0},
    "status_pegawai": {"value": "Tetap", "confidence": 1},
    "gaji_pokok": {"value": 4500000, "confidence": 1},
    "tunjangan_jabatan": {"value": 500000, "confidence": 1},
    "tunjangan_transport": {"value": 300000, "confidence": 1},
    "tunjangan_makan": {"value": 600000, "confidence": 1},
    "tunjangan_komunikasi": {"value": 150000, "confidence": 0},
    "tunjangan_lain": {"value": 200000, "confidence": 0},
    "bonus": {"value": None, "confidence": 0},
    "insentif": {"value": 250000, "confidence": 1},
    "lembur": {"value": 180000, "confidence": 1},
    "thr": {"value": None, "confidence": 0},
    "total_pendapatan": {"value": 6680000, "confidence": 1},
    "total_potongan": {"value": 225000, "confidence": 1},
    "gaji_bersih": {"value": 6455000, "confidence": 1},
    "missing_mandatory_fields": [],
}
_DATA = {
    "total_slip": 3,
    "slip": [
        _SLIP,
        {**_SLIP, "page": 2, "periode": {"value": "2025-03", "confidence": 1}},
        {**_SLIP, "page": 3, "periode": {"value": "2025-04", "confidence": 1}},
    ],
}
_COMPLETED = extract_body(200, COMPLETED_MESSAGE, data=_DATA, guardrails=0, request_id=RID)
_GUARDRAILS_ONLY = extract_body(
    200,
    COMPLETED_MESSAGE,
    data={
        "passed": True,
        "reason": None,
        "document": {
            "verdict": "accepted",
            "confidence": 0.9934,
            "n_pages": 1,
            "threshold": 0.47,
        },
        "pages": [],
        "checks": {
            "blank": {"passed": True},
            "blur": {"passed": True, "p_broken": 0.0153},
            "identity": {"passed": True, "proba_slip_gaji": 0.9934},
        },
        "skipped": [],
        "unavailable": [],
    },
    guardrails=0,
    request_id=RID,
)
_PROCESSING = extract_body(202, PROCESSING_MESSAGE, request_id=RID)
_REJECTED = extract_body(
    400,
    "Dokumen ini bukan slip gaji. Mohon unggah slip gaji.",
    errors=REJECTED_CODE,
    guardrails=1,
    request_id=RID,
    pipeline_last_stage="guardrails",
)
_FAILED = extract_body(
    422,
    "tidak ada teks OCR yang bisa distrukturkan",
    errors="STRUCTURING_FAILED",
    guardrails=0,
    request_id=RID,
    pipeline_last_stage="structuring",
)
_INVALID_SEQUENCE = extract_body(
    422,
    "Invalid pipeline_name_sequence: services must keep the order guardrails -> extraction -> structuring -> "
    "scoring without skipping one in the middle",
    errors="INVALID_PIPELINE_SEQUENCE",
    request_id=RID,
    pipeline_last_stage=ENTRY,
)

_CONTRACT_TABLE = (
    "| Hasil | HTTP | `data` | `guardrails` | `errors` | `pipeline_last_stage` |\n"
    "|---|---|---|---|---|---|\n"
    "| Selesai | 200 | hasil service terakhir | `0` | null | null |\n"
    "| Masih berjalan | 202 | null | null | null | null |\n"
    f"| Ditahan guardrail | 400 | null | `1` | `{REJECTED_CODE}` | `guardrails` |\n"
    f"| Ditolak aturan structuring | 400 | null | `1` | `{REJECTED_CODE}` | `structuring` |\n"
    "| Sebuah tahap gagal | 422 | null | `0` | `OCR_FAILED` / `STRUCTURING_FAILED` / `SCORING_FAILED` | "
    "tahap itu |\n\n"
    "Status HTTP (juga `status_code`) menyatakan keadaan permintaan: 200 selesai, 202 masih berjalan, 4xx / 5xx "
    "gagal atau ditolak. `pipeline_last_stage` null pada jawaban sukses (200, 202) dan menyebut service asal "
    "galat, atau `orchestrator` bila ditolak pintu masuk sendiri.\n\n"
)
_SEQUENCE_NOTE = (
    "**`pipeline_name_sequence` (saklar service).** Urutan baku `guardrails -> extraction -> structuring -> "
    "scoring`; `guardrails` boleh dilewati dari depan, ekor boleh dipotong, tengah tidak boleh dilewati; "
    "pelanggaran 422 `INVALID_PIPELINE_SEQUENCE`. Service terakhir mengakhiri permintaan dan hasilnya menjadi "
    "`data` apa adanya.\n\n"
    "**Guardrail slip gaji membaca teks OCR** (AUC 0,911, piksel hanya 0,638), jadi `guardrails` dijalankan "
    "tahap OCR setelah dokumen dibaca: ketiga service guardrail-blank, guardrail-blur, guardrail-identity "
    'dipanggil bersamaan, masing-masing bisa dimatikan sendiri. Akibatnya `["guardrails"]` pun menjalankan '
    "OCR, dan bisa dijawab 202 bila melewati masa tunggu."
)
_MULTI_SLIP = (
    "**Satu dokumen bisa berisi beberapa slip** (tiga bulan dalam satu berkas adalah bentuk yang paling sering "
    "diunggah), jadi `data` pipeline penuh berisi `total_slip` dan array `slip`, satu entri per halaman."
)


def _parse_sequence(raw: list[str] | None) -> list[str] | None:
    """Repeated form fields, or one JSON array string. None/empty: the full pipeline."""
    if not raw:
        return None
    names: list[str] = []
    for item in raw:
        text = (item or "").strip()
        if not text:
            continue
        if text.startswith("["):
            try:
                value = json.loads(text)
            except ValueError as exc:
                raise InvalidSequence("Invalid pipeline_name_sequence: not a JSON array") from exc
            if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
                raise InvalidSequence("Invalid pipeline_name_sequence: must be an array of service names")
            names.extend(value)
        else:
            names.append(text)
    if not names:
        return None
    return list(validate_sequence(names))


@router.post(
    "/v1/extract-ocr",
    response_model=ExtractOcrResponse,
    operation_id="extractOcr",
    summary="Jalankan pipeline atas sebuah dokumen slip gaji, jawab hasilnya atau 202",
    description=(
        "**Satu-satunya panggilan Orkestrasi pusat.** Memeriksa berkas (tipe, `MAX_UPLOAD_BYTES` 2,5 MB, "
        "`MAX_DOCUMENT_PAGES`), menyerahkannya ke tahap OCR, lalu menunggu sampai service terakhir "
        "`pipeline_name_sequence` selama `PIPELINE_WAIT_SECONDS` (bawaan 15 detik). Bila belum selesai: 202 dan "
        "hasil dikirim lewat callback, atau dibaca dengan `GET /v1/extract-ocr/{request_id}`.\n\n"
        + _CONTRACT_TABLE
        + _SEQUENCE_NOTE
        + "\n\n"
        + _MULTI_SLIP
        + "\n\n**Confidence.** Setiap field `{value, confidence}`; `confidence` 1 ketika probabilitas model "
        "keyakinan (0-1) >= ambang field itu, selain itu 0. Callback hasil membawa `data` yang sama persis.\n\n"
        "**Ambang per permintaan, semuanya sisi accept (tanpa `guardrails_tendency`).** "
        '`guardrails_confidence_threshold` = JSON object per guardrail: `{"acc_rej": 0.8}` (= `identity`, '
        'P(slip gaji)) dan/atau `{"blur": 0.7}` (P(terbaca) = 1 - p_broken), 0 < x < 1. Tidak dikirim: ambang '
        "bawaan tiap service guardrail. `column_confidence_threshold` = JSON object per field; `all_field` "
        "disebar ke ke-20 field dan kunci field sendiri menang. Field yang tidak disebut: "
        "`FIELD_CONFIDENCE_THRESHOLD` (0,5). Ambang yang tidak terbaca atau kunci tak dikenal: 422 "
        "`INVALID_THRESHOLD`, tidak ada yang dijalankan.\n\n"
        "**Idempotensi.** request_id yang sama tidak menjalankan pipeline dua kali kecuali percobaan OCR "
        "sebelumnya `FAILED` atau melewati sewa job (`PIPELINE_JOB_LEASE_SECONDS`)."
    ),
    responses={
        200: success_examples(
            "Selesai di dalam masa tunggu",
            completed=("Pipeline penuh: satu berkas tiga bulan", _COMPLETED),
            guardrails_only=('`["guardrails"]`: laporan guardrail', _GUARDRAILS_ONLY),
        ),
        202: {
            **success_examples("Masih berjalan saat masa tunggu habis", processing=("Masih diproses", _PROCESSING)),
            "model": ExtractOcrResponse,
        },
        400: {
            "model": ExtractOcrResponse,
            "description": (
                f"Ditahan guardrail atau ditolak aturan structuring (`{REJECTED_CODE}`, `guardrails: 1`), "
                "`UNSUPPORTED_DOCUMENT_TYPE`, `EMPTY_FILE`, `UNSUPPORTED_FILE_TYPE`, `UNREADABLE_FILE`, "
                f"`TOO_MANY_PAGES` (`{TOO_MANY_PAGES_MESSAGE}`), `INVALID_FILE_SOURCE`, `FILE_URL_REJECTED`"
            ),
            "content": {"application/json": {"example": _REJECTED}},
        },
        401: UNAUTHORIZED,
        413: error(
            413,
            "Dokumen melebihi `MAX_UPLOAD_BYTES` (2,5 MB); tidak ada yang dijalankan",
            PAYLOAD_TOO_LARGE_MESSAGE.format(limit="2,5 MB"),
            errors="FILE_TOO_LARGE",
        ),
        422: {
            "model": ExtractOcrResponse,
            "description": (
                "`INVALID_PIPELINE_SEQUENCE`, `INVALID_THRESHOLD`, `VALIDATION_ERROR` (tidak ada "
                "yang dijalankan), atau sebuah tahap gagal: `OCR_FAILED`, `STRUCTURING_FAILED`, `SCORING_FAILED`"
            ),
            "content": {"application/json": {"example": _INVALID_SEQUENCE}},
        },
        500: error(
            500, "Service extraction menjawab bentuk tak terduga", "extraction service returned an unexpected response"
        ),
        503: error(
            503, "Service extraction tidak terjangkau; tidak ada yang dijalankan", "extraction service is unavailable"
        ),
        504: error(504, "Service extraction tidak menjawab tepat waktu", "extraction service timed out after 10.0s"),
    },
)
async def extract_ocr(
    request: Request,
    response: Response,
    request_id: str = Form(..., description="request_id yang dibuat Orkestrasi pusat", examples=[RID]),
    document_type: str = Form(
        DOCUMENT_TYPE,
        description=f"Hanya `{DOCUMENT_TYPE}`; selain itu 400 `UNSUPPORTED_DOCUMENT_TYPE`",
        examples=[DOCUMENT_TYPE],
    ),
    file: UploadFile | str | None = FileField,
    file_url: str | None = FileUrlField,
    pipeline_name_sequence: list[str] | None = Form(
        None,
        description=(
            "Service yang dijalankan, berurutan: `guardrails`, `extraction`, `structuring`, `scoring` — JSON array "
            "atau field berulang. Tidak dikirim: keempatnya"
        ),
        examples=[DEFAULT_SEQUENCE_EXAMPLE],
    ),
    guardrails_confidence_threshold: str | None = Form(
        None,
        description=(
            'JSON object per guardrail, sisi accept, 0 < x < 1: `{"acc_rej": 0.8}` (= `identity`, P(slip gaji)), '
            '`{"blur": 0.7}` (P(terbaca)). Tidak dikirim: ambang bawaan tiap service guardrail'
        ),
        examples=['{"acc_rej": 0.8}'],
    ),
    column_confidence_threshold: str | None = Form(
        None,
        description=(
            'JSON per field, sisi accept: `{"all_field": 0.8}` untuk semua field, atau `{"gaji_bersih": 0.9}`; '
            "kunci field sendiri menang atas `all_field`. Field yang tidak disebut: `FIELD_CONFIDENCE_THRESHOLD` (0,5)"
        ),
        examples=['{"all_field": 0.8}'],
    ),
    service: ExtractOcrService = Depends(get_extract_service),
    settings: Settings = Depends(get_settings),
):
    received_at = time.monotonic()

    def refuse(status: int, message: str, code: str) -> dict[str, Any]:
        response.status_code = status
        return extract_body(status, message, errors=code, request_id=request_id, pipeline_last_stage=ENTRY)

    try:
        sequence = _parse_sequence(pipeline_name_sequence)
    except InvalidSequence as exc:
        return refuse(422, str(exc), "INVALID_PIPELINE_SEQUENCE")
    try:
        guardrail_thresholds = parse_guardrails_threshold(guardrails_confidence_threshold)
        columns = parse_column_thresholds(column_confidence_threshold)
    except InvalidThreshold as exc:
        return refuse(422, str(exc), "INVALID_THRESHOLD")
    if document_type != DOCUMENT_TYPE:
        return refuse(
            400, f"Unsupported document_type: {document_type}. Supported: {DOCUMENT_TYPE}", "UNSUPPORTED_DOCUMENT_TYPE"
        )

    # request_id dari Orkestrasi pusat menjadi id permintaan ini: di amplop galat yang dilempar di bawah
    # (413, berkas buruk, tahap tak terjangkau), di header respons X-Request-ID dan panggilan keluar, serta
    # di baris log kita — jadi satu id mengikuti permintaan melewati seluruh tahap.
    token = adopt_request_id(request, request_id)
    try:
        content, filename, content_type = await read_image(request, file, file_url)
        outcome = await service.submit(
            request_id,
            document_type,
            filename,
            content_type,
            content,
            received_at=received_at,
            file_url=file_url,
            sequence=sequence,
            guardrail_thresholds=guardrail_thresholds,
            columns=columns,
        )
    finally:
        reset_request_id(token)
    status_code, body = extract_response(outcome, request_id=request_id, threshold=settings.field_confidence_threshold)
    response.status_code = status_code
    return body


@router.get(
    "/v1/extract-ocr/{request_id}",
    response_model=ExtractOcrResponse,
    operation_id="getExtractOcr",
    summary="Di mana sebuah permintaan sekarang: jawaban extract-ocr, tanpa menunggu",
    description=(
        "Membaca job tiap tahap milik `request_id` sekali, sampai service terakhir `pipeline_name_sequence` "
        "permintaan itu (tersimpan bersama job OCR), dan menjawab dengan kontrak yang sama seperti "
        "`POST /v1/extract-ocr` — termasuk confidence menurut `column_confidence_threshold` yang dikirim saat "
        "POST:\n\n" + _CONTRACT_TABLE + "**404** `REQUEST_ID_NOT_FOUND`: tidak ada job untuk "
        "request_id ini.\n\n"
        "**Batasan.** Penyerahan antar dua tahap yang gagal permanen membuat tahap berikutnya tidak punya job, "
        "sehingga endpoint ini terus menjawab `202`. Callback `FAILED` dan tabel Orkestrasi pusat yang menyimpan "
        "keadaan akhir itu."
    ),
    responses={
        200: success_examples("Selesai", completed=("Hasil OCR", _COMPLETED)),
        202: {
            **success_examples("Masih berjalan", processing=("Masih diproses", _PROCESSING)),
            "model": ExtractOcrResponse,
        },
        400: {
            "model": ExtractOcrResponse,
            "description": f"Ditahan guardrail atau ditolak aturan structuring (`{REJECTED_CODE}`, `guardrails: 1`)",
            "content": {"application/json": {"example": _REJECTED}},
        },
        401: UNAUTHORIZED,
        404: error(
            404,
            "Tidak ada tahap yang punya job untuk request_id ini",
            f"No request found for request_id {RID}",
            errors="REQUEST_ID_NOT_FOUND",
        ),
        422: {
            "model": ExtractOcrResponse,
            "description": "Sebuah tahap gagal (`OCR_FAILED`, `STRUCTURING_FAILED`, `SCORING_FAILED`)",
            "content": {"application/json": {"example": _FAILED}},
        },
        500: error(
            500,
            "Sebuah tahap menjawab bentuk tak terduga",
            "structuring service error (401): Invalid or missing API key",
        ),
        503: error(503, "Sebuah tahap tidak terjangkau", "structuring service is unavailable"),
        504: error(504, "Sebuah tahap tidak menjawab tepat waktu", "structuring service timed out after 10.0s"),
    },
)
async def get_extract_ocr(
    request_id: str,
    request: Request,
    response: Response,
    service: ExtractOcrService = Depends(get_extract_service),
    settings: Settings = Depends(get_settings),
):
    token = adopt_request_id(request, request_id)
    try:
        outcome = await service.status(request_id)
    finally:
        reset_request_id(token)
    status_code, body = extract_response(outcome, request_id=request_id, threshold=settings.field_confidence_threshold)
    response.status_code = status_code
    return body
