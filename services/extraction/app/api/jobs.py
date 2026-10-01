import json
from typing import Any

from fastapi import APIRouter, Depends, Form, Request, UploadFile

from ocr_common.errors import UnprocessableEntity
from ocr_common.pipeline import InvalidSequence, StagePipeline, validate_sequence
from ocr_common.pipeline.outbox_status import (
    OUTBOX_RELEASE_DESCRIPTION,
    OUTBOX_RELEASE_SUMMARY,
    OUTBOX_STATUS_DESCRIPTION,
    OUTBOX_STATUS_SUMMARY,
    OutboxReleaseResponse,
    OutboxStatusResponse,
    outbox_release,
    outbox_release_responses,
    outbox_status,
    outbox_status_responses,
)
from ocr_common.slip_gaji import DOCUMENT_TYPE
from ocr_common.web.envelope import envelope
from ocr_common.web.intake import FileField, FileUrlField, resolve_intake
from ocr_common.web.request_id import get_request_id
from ocr_common.web.schemas import REQUEST_ID_EXAMPLE, UNAUTHORIZED, JobAcceptedResponse, error, success_examples
from ocr_common.web.security import verify_api_key

from app.api.extraction import OCR_RESULT_EXAMPLE
from app.api.schemas import OcrJobStatusResponse
from app.dependencies import get_job_service, get_pipeline
from app.services.job_service import ExtractionJobService, Source

router = APIRouter(tags=["Pipeline"], dependencies=[Depends(verify_api_key)])

_JOB = {"request_id": REQUEST_ID_EXAMPLE, "stage": "OCR", "created_at": "2026-09-18T04:00:00+00:00"}


@router.post(
    "/v1/extraction/jobs",
    status_code=202,
    response_model=JobAcceptedResponse,
    operation_id="submitOcrJob",
    summary="Mulai pipeline untuk sebuah dokumen (tahap OCR)",
    description=(
        "**Langkah 1 pipeline, asinkron: awal rantai OCR -> structuring -> scoring.** Dipanggil orchestrator "
        "Slip Gaji dari `POST /v1/extract-ocr`; Orkestrasi pusat tidak memanggilnya.\n\n"
        "Mencatat job (`ocr_jobs`, idempoten per request_id), menjawab **202 seketika**, lalu di latar: membaca "
        "dokumen (`file`, atau mengunduh `file_url`), menjalankan OCR, **meminta guardrail menilai teksnya**, "
        "menyimpan hasil (`ocr_results`), mengirim callback `OCR`, dan menyerahkan job ke service structuring.\n\n"
        "**Guardrail berjalan di sini, bukan sebelum pipeline.** Model guardrail slip gaji membaca teks OCR "
        "(AUC 0,908) dan bukan piksel (0,638), jadi ia baru bisa menilai setelah tahap ini membaca dokumen. "
        "Dokumen yang ditahan membuat job ini tetap `DONE` — hasil OCR-nya tersimpan dan bisa ditelusuri — "
        "tetapi tidak diteruskan ke structuring, dan orchestrator menjawab 400 dengan `guardrails: 1`.\n\n"
        "**Tiga guardrail, masing-masing bisa mati sendiri.** guardrail-blank, guardrail-blur dan "
        "guardrail-identity dipanggil bersamaan. Yang dimatikan (`GUARDRAIL_<NAMA>_ENABLED=false`) tercatat di "
        "`skipped`; yang menyala tetapi tidak menjawab tercatat di `unavailable` selama `GUARDRAILS_FAIL_OPEN` "
        "menyala (bawaan) — dua yang lain tetap memutuskan. Dimatikan, galat guardrail menjadi job `FAILED`.\n\n"
        "**Dokumen.** Kirim `file` (multipart) atau `file_url`, tepat salah satu. JPEG, PNG atau PDF, paling "
        "besar `MAX_UPLOAD_BYTES` (2,5 MB bawaan). `file_url` diunduh di latar, jadi buat presigned URL berumur "
        "lebih panjang daripada antrean terburuk; URL kedaluwarsa menjadi job `FAILED`, bukan `4xx`.\n\n"
        "**Idempotensi.** request_id yang sama menjawab `202` dengan `duplicate: true` dan tidak menjalankan OCR "
        "dua kali, kecuali percobaan sebelumnya `FAILED` atau sudah `PROCESSING` lebih lama daripada sewa job "
        "(`PIPELINE_JOB_LEASE_SECONDS`, bawaan 5 menit)."
    ),
    responses={
        202: success_examples(
            "The job was accepted (or already existed)",
            accepted=(
                "New job",
                envelope(
                    202,
                    "Accepted",
                    {"request_id": REQUEST_ID_EXAMPLE, "stage": "OCR", "status": "PROCESSING", "duplicate": False},
                    REQUEST_ID_EXAMPLE,
                ),
            ),
            duplicate=(
                "Same request_id sent again: nothing is re-run, `status` is the existing job's status",
                envelope(
                    202,
                    "Accepted",
                    {"request_id": REQUEST_ID_EXAMPLE, "stage": "OCR", "status": "DONE", "duplicate": True},
                    REQUEST_ID_EXAMPLE,
                ),
            ),
        ),
        400: error(
            400,
            "Tidak satu pun atau keduanya dari file / file_url",
            "Tidak ada berkas yang diterima. Lampirkan `file` (unggahan), atau isi `file_url`",
        ),
        401: UNAUTHORIZED,
        422: error(422, "Validation Error", "body.request_id: Field required", errors="VALIDATION_ERROR"),
    },
)
async def submit_job(
    request_id: str = Form(..., description="request_id yang dibuat orchestrator", examples=[REQUEST_ID_EXAMPLE]),
    document_type: str = Form(
        DOCUMENT_TYPE,
        description=f"Jenis dokumen pilihan klien. Hanya `{DOCUMENT_TYPE}` yang didukung",
        examples=[DOCUMENT_TYPE],
    ),
    pipeline_name_sequence: str | None = Form(
        None,
        description=(
            "JSON array, sudah diperiksa orchestrator: `guardrails` -> `extraction` -> `structuring` -> `scoring`. "
            "Tidak dikirim: keempatnya. Tanpa `guardrails` ketiga guardrail tidak dipanggil; berakhir di "
            "`guardrails` atau `extraction` membuat job ini mengakhiri permintaan"
        ),
        examples=['["guardrails","extraction","structuring","scoring"]'],
    ),
    guardrails_confidence_threshold: str | None = Form(
        None,
        description=(
            "Ambang guardrail permintaan ini dalam bentuk ternormalisasi orchestrator: "
            '`{"identity": {"value": 0.8, "target": "accept"}, "blur": {...}}`'
        ),
    ),
    column_confidence_threshold: str | None = Form(
        None, description="JSON `{field: 0-1}` (boleh `all_field`); diteruskan ke scoring"
    ),
    file: UploadFile | str | None = FileField,
    file_url: str | None = FileUrlField,
    service: ExtractionJobService = Depends(get_job_service),
):
    try:
        # Every valid sequence starts here: with `guardrails` (a step of this OCR job) or with `extraction`.
        names = _json(pipeline_name_sequence, list)
        sequence = list(validate_sequence(names)) if names else None
        thresholds = _json(guardrails_confidence_threshold, dict)
        columns = _json(column_confidence_threshold, dict)
    except (ValueError, InvalidSequence) as exc:
        raise UnprocessableEntity(str(exc)) from exc
    upload, url = resolve_intake(file, file_url)
    source: Source
    if upload is not None:
        source = (await upload.read(), upload.filename or "", upload.content_type)
    else:
        assert url is not None
        source = url
    data = await service.submit(
        request_id, document_type, source, sequence=sequence, guardrail_thresholds=thresholds, columns=columns
    )
    return envelope(202, "Accepted", data, request_id)


def _json(raw: str | None, kind: type) -> Any:
    """A JSON form field of the given kind, or None when not sent."""
    if raw is None or not raw.strip():
        return None
    value = json.loads(raw)
    if not isinstance(value, kind):
        raise ValueError(f"expected a JSON {kind.__name__}")
    return value


@router.get(
    "/v1/extraction/jobs/{request_id}",
    response_model=OcrJobStatusResponse,
    operation_id="getOcrJob",
    summary="Status and result of the OCR stage",
    description=(
        "Status of this stage only, and its raw OCR result once `DONE`. Internal: the orchestrator Slip Gaji reads it "
        "(while it waits, and for its `GET /v1/extract-ocr/{request_id}`), and it helps to debug. The later "
        "stages have the same endpoint on their own service (`/v1/structuring/jobs/{request_id}`, "
        "`/v1/scoring/jobs/{request_id}`)."
    ),
    responses={
        200: success_examples(
            "The job exists",
            processing=(
                "Still running",
                envelope(
                    200,
                    "Success",
                    {
                        **_JOB,
                        "status": "PROCESSING",
                        "error_message": None,
                        "result": None,
                        "updated_at": "2026-09-18T04:00:00+00:00",
                    },
                    REQUEST_ID_EXAMPLE,
                ),
            ),
            done=(
                "Finished",
                envelope(
                    200,
                    "Success",
                    {
                        **_JOB,
                        "status": "DONE",
                        "error_message": None,
                        "result": OCR_RESULT_EXAMPLE,
                        "updated_at": "2026-09-18T04:00:01+00:00",
                    },
                    REQUEST_ID_EXAMPLE,
                ),
            ),
            failed=(
                "Failed: same `error_message` as the FAILED callback. Resubmitting the request_id runs it again",
                envelope(
                    200,
                    "Success",
                    {
                        **_JOB,
                        "status": "FAILED",
                        "error_message": "extraction OCR model is unavailable",
                        "result": None,
                        "updated_at": "2026-09-18T04:00:03+00:00",
                    },
                    REQUEST_ID_EXAMPLE,
                ),
            ),
        ),
        401: UNAUTHORIZED,
        404: error(
            404,
            "No OCR job for this request_id",
            f"No OCR job found for request_id: {REQUEST_ID_EXAMPLE}",
            request_id=REQUEST_ID_EXAMPLE,
        ),
        422: error(
            422,
            "Validation Error",
            "path.request_id: Field required",
            request_id=REQUEST_ID_EXAMPLE,
            errors="VALIDATION_ERROR",
        ),
    },
)
async def get_job(request_id: str, service: ExtractionJobService = Depends(get_job_service)):
    data = await service.get(request_id)
    return envelope(200, "Success", data, request_id)


@router.get(
    "/v1/extraction/outbox",
    response_model=OutboxStatusResponse,
    operation_id="getExtractionOutboxStatus",
    summary=OUTBOX_STATUS_SUMMARY,
    description=OUTBOX_STATUS_DESCRIPTION,
    responses=outbox_status_responses("OCR"),
)
async def get_outbox_status(request: Request, pipeline: StagePipeline = Depends(get_pipeline)):
    return envelope(200, "Success", await outbox_status(pipeline), get_request_id(request))


@router.post(
    "/v1/extraction/outbox/release",
    response_model=OutboxReleaseResponse,
    operation_id="releaseOcrOutbox",
    summary=OUTBOX_RELEASE_SUMMARY,
    description=OUTBOX_RELEASE_DESCRIPTION,
    responses=outbox_release_responses("OCR"),
)
async def release_outbox(
    request: Request, request_id: str | None = None, pipeline: StagePipeline = Depends(get_pipeline)
):
    data = await outbox_release(pipeline, request_id)
    return envelope(200, "Success", data, request_id or get_request_id(request))
