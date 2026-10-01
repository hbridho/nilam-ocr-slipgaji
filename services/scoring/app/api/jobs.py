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

from app.api.schemas import ScoringJobRequest, ScoringJobStatusResponse
from app.api.scoring import SCORING_EXAMPLE
from app.dependencies import get_job_service, get_pipeline
from app.services.job_service import ScoringJobService

router = APIRouter(tags=["Pipeline"], dependencies=[Depends(verify_api_key)])

_JOB = {"request_id": REQUEST_ID_EXAMPLE, "stage": "SCORING", "created_at": "2026-09-18T04:00:01+00:00"}


@router.post(
    "/v1/scoring/jobs",
    status_code=202,
    response_model=JobAcceptedResponse,
    operation_id="submitScoringJob",
    summary="Serahkan dokumen terstruktur ke tahap scoring (terakhir)",
    description=(
        "**Langkah 3 pipeline, asinkron, tahap terakhir. Dipanggil service structuring, bukan orchestrator.**\n\n"
        "Mencatat job (`scoring_jobs`, idempoten per request_id), menjawab **202 seketika**, lalu di latar: "
        "memberi skor keyakinan pada setiap field tiap slip, menyimpan hasilnya (`scoring_results`), dan "
        "mengirim callback `SCORING` yang membawa **hasil akhir** permintaan ini.\n\n"
        "Keluarannya skor 0-1 per field. Tidak ada skor tingkat dokumen dan tidak ada keputusan "
        "terima / tolak: ambang milik orchestrator."
    ),
    responses={
        202: success_examples(
            "The job was accepted (or already existed)",
            accepted=(
                "New job",
                envelope(
                    202,
                    "Accepted",
                    {"request_id": REQUEST_ID_EXAMPLE, "stage": "SCORING", "status": "PROCESSING", "duplicate": False},
                    REQUEST_ID_EXAMPLE,
                ),
            ),
            duplicate=(
                "Same request_id sent again: nothing is re-run",
                envelope(
                    202,
                    "Accepted",
                    {"request_id": REQUEST_ID_EXAMPLE, "stage": "SCORING", "status": "DONE", "duplicate": True},
                    REQUEST_ID_EXAMPLE,
                ),
            ),
        ),
        401: UNAUTHORIZED,
        422: error(422, "Validation Error", "body.request_id: Field required", errors="VALIDATION_ERROR"),
    },
)
async def submit_job(body: ScoringJobRequest, service: ScoringJobService = Depends(get_job_service)):
    guardrails = body.guardrails.model_dump(exclude_unset=True) if body.guardrails is not None else None
    data = await service.submit(
        body.request_id,
        body.document_type,
        guardrails,
        body.ocr,
        body.structuring,
        sequence=body.pipeline_name_sequence,
        columns=body.column_confidence_threshold,
    )
    return envelope(202, "Accepted", data, body.request_id)


@router.get(
    "/v1/scoring/jobs/{request_id}",
    response_model=ScoringJobStatusResponse,
    operation_id="getScoringJob",
    summary="Status and result of the scoring stage",
    description=(
        "Status of this stage only and, once `DONE`, the two confidences plus the exact payload that was scored. "
        "Internal: the orchestrator Slip Gaji reads it (while it waits, and for its `GET "
        "/v1/extract-ocr/{request_id}`); "
        "the central orchestrator receives the final result in the callback. Also useful to audit a score."
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
                        "result": SCORING_EXAMPLE,
                        "updated_at": "2026-09-18T04:00:01+00:00",
                    },
                    REQUEST_ID_EXAMPLE,
                ),
            ),
            failed=(
                "Failed",
                envelope(
                    200,
                    "Success",
                    {
                        **_JOB,
                        "status": "FAILED",
                        "error_message": "Unsupported document_type: ktp. Supported: ['slip_gaji']",
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
            "No scoring job for this request_id",
            f"No SCORING job found for request_id: {REQUEST_ID_EXAMPLE}",
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
async def get_job(request_id: str, service: ScoringJobService = Depends(get_job_service)):
    data = await service.get(request_id)
    return envelope(200, "Success", data, request_id)


@router.get(
    "/v1/scoring/outbox",
    response_model=OutboxStatusResponse,
    operation_id="getScoringOutboxStatus",
    summary=OUTBOX_STATUS_SUMMARY,
    description=OUTBOX_STATUS_DESCRIPTION,
    responses=outbox_status_responses("SCORING"),
)
async def get_outbox_status(request: Request, pipeline: StagePipeline = Depends(get_pipeline)):
    return envelope(200, "Success", await outbox_status(pipeline), get_request_id(request))


@router.post(
    "/v1/scoring/outbox/release",
    response_model=OutboxReleaseResponse,
    operation_id="releaseScoringOutbox",
    summary=OUTBOX_RELEASE_SUMMARY,
    description=OUTBOX_RELEASE_DESCRIPTION,
    responses=outbox_release_responses("SCORING"),
)
async def release_outbox(
    request: Request, request_id: str | None = None, pipeline: StagePipeline = Depends(get_pipeline)
):
    data = await outbox_release(pipeline, request_id)
    return envelope(200, "Success", data, request_id or get_request_id(request))
