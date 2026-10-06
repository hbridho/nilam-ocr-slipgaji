from fastapi import APIRouter, Depends, Request

from ocr_common.pipeline import StagePipeline
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
from ocr_common.web.envelope import envelope
from ocr_common.web.request_id import get_request_id
from ocr_common.web.schemas import REQUEST_ID_EXAMPLE, UNAUTHORIZED, JobAcceptedResponse, error, success_examples
from ocr_common.web.security import verify_api_key

from app.api.direct import STRUCTURED_EXAMPLE
from app.api.schemas import StructuringJobRequest, StructuringJobStatusResponse
from app.dependencies import get_job_service, get_pipeline
from app.services.job_service import StructuringJobService

router = APIRouter(tags=["Pipeline"], dependencies=[Depends(verify_api_key)])

_JOB = {"request_id": REQUEST_ID_EXAMPLE, "stage": "STRUCTURING", "created_at": "2026-09-18T04:00:01+00:00"}


@router.post(
    "/v1/structuring/jobs",
    status_code=202,
    response_model=JobAcceptedResponse,
    operation_id="submitStructuringJob",
    summary="Serahkan hasil OCR ke tahap structuring",
    description=(
        "**Langkah 2 pipeline, asinkron. Dipanggil service OCR, bukan oleh orchestrator.**\n\n"
        "Mencatat job (`structuring_jobs`, idempoten per request_id), menjawab **202 seketika**, lalu di latar: "
        "mengubah teks OCR tiap halaman menjadi 20 field slip gaji, menyimpan hasilnya "
        "(`structuring_results`), mengirim callback `STRUCTURING`, dan menyerahkan job (laporan guardrail + "
        "hasil OCR + hasil structuring) ke service scoring.\n\n"
        "**Satu halaman = satu slip**: berkas tiga bulan menghasilkan tiga slip, masing-masing dengan 20 "
        "fieldnya sendiri. Dokumen tanpa satu pun halaman berteks menggagalkan job dengan alasannya."
    ),
    responses={
        202: success_examples(
            "The job was accepted (or already existed)",
            accepted=(
                "New job",
                envelope(
                    202,
                    "Accepted",
                    {
                        "request_id": REQUEST_ID_EXAMPLE,
                        "stage": "STRUCTURING",
                        "status": "PROCESSING",
                        "duplicate": False,
                    },
                    REQUEST_ID_EXAMPLE,
                ),
            ),
            duplicate=(
                "Same request_id sent again: nothing is re-run",
                envelope(
                    202,
                    "Accepted",
                    {"request_id": REQUEST_ID_EXAMPLE, "stage": "STRUCTURING", "status": "DONE", "duplicate": True},
                    REQUEST_ID_EXAMPLE,
                ),
            ),
        ),
        401: UNAUTHORIZED,
        422: error(422, "Validation Error", "body.request_id: Field required", errors="VALIDATION_ERROR"),
    },
)
async def submit_job(body: StructuringJobRequest, service: StructuringJobService = Depends(get_job_service)):
    guardrails = body.guardrails.model_dump(exclude_unset=True) if body.guardrails is not None else None
    data = await service.submit(
        body.request_id,
        body.document_type,
        guardrails,
        body.ocr,
        sequence=body.pipeline_name_sequence,
        columns=body.column_confidence_threshold,
    )
    return envelope(202, "Accepted", data, body.request_id)


@router.get(
    "/v1/structuring/jobs/{request_id}",
    response_model=StructuringJobStatusResponse,
    operation_id="getStructuringJob",
    summary="Status dan hasil tahap structuring",
    description=(
        "Status tahap ini saja, beserta slip yang sudah terstruktur setelah `DONE`. Internal: orchestrator "
        "membacanya (selama menunggu, dan untuk `GET /v1/extract-ocr/{request_id}`), dan berguna untuk "
        "menelusuri masalah."
    ),
    responses={
        200: success_examples(
            "The job exists",
            done=(
                "Finished",
                envelope(
                    200,
                    "Success",
                    {
                        **_JOB,
                        "status": "DONE",
                        "error_message": None,
                        "result": STRUCTURED_EXAMPLE,
                        "updated_at": "2026-09-18T04:00:01+00:00",
                    },
                    REQUEST_ID_EXAMPLE,
                ),
            ),
            failed=(
                "Gagal: tidak ada halaman berteks untuk distrukturkan",
                envelope(
                    200,
                    "Success",
                    {
                        **_JOB,
                        "status": "FAILED",
                        "error_message": "tidak ada teks OCR yang bisa distrukturkan",
                        "result": None,
                        "updated_at": "2026-09-18T04:00:01+00:00",
                    },
                    REQUEST_ID_EXAMPLE,
                ),
            ),
        ),
        401: UNAUTHORIZED,
        404: error(
            404,
            "No structuring job for this request_id",
            f"No STRUCTURING job found for request_id: {REQUEST_ID_EXAMPLE}",
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
async def get_job(request_id: str, service: StructuringJobService = Depends(get_job_service)):
    data = await service.get(request_id)
    return envelope(200, "Success", data, request_id)


@router.get(
    "/v1/structuring/outbox",
    response_model=OutboxStatusResponse,
    operation_id="getStructuringOutboxStatus",
    summary=OUTBOX_STATUS_SUMMARY,
    description=OUTBOX_STATUS_DESCRIPTION,
    responses=outbox_status_responses("STRUCTURING"),
)
async def get_outbox_status(request: Request, pipeline: StagePipeline = Depends(get_pipeline)):
    return envelope(200, "Success", await outbox_status(pipeline), get_request_id(request))


@router.post(
    "/v1/structuring/outbox/release",
    response_model=OutboxReleaseResponse,
    operation_id="releaseStructuringOutbox",
    summary=OUTBOX_RELEASE_SUMMARY,
    description=OUTBOX_RELEASE_DESCRIPTION,
    responses=outbox_release_responses("STRUCTURING"),
)
async def release_outbox(
    request: Request, request_id: str | None = None, pipeline: StagePipeline = Depends(get_pipeline)
):
    data = await outbox_release(pipeline, request_id)
    return envelope(200, "Success", data, request_id or get_request_id(request))
