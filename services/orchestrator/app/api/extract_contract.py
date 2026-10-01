"""The `extract-ocr` response of API spec [07]: the standard envelope plus `guardrails` and
`pipeline_last_stage`, and `data` shaped by the last service of `pipeline_name_sequence`."""

from collections.abc import Mapping
from typing import Any

from ocr_common.pipeline import (
    GUARDRAILS,
    SERVICE_OF_STAGE,
    STAGE_OCR,
    STAGE_STRUCTURING,
    STATUS_DONE,
    STATUS_FAILED,
    last_service,
)
from ocr_common.slip_gaji import REJECTED_CODE, contract_data, guardrails_data, ocr_data, structuring_data
from ocr_common.web.envelope import envelope

from app.services.pipeline_waiter import STATUS_REJECTED

COMPLETED_MESSAGE = "OCR extraction completed successfully"
PROCESSING_MESSAGE = "OCR job accepted; still processing"

# `pipeline_last_stage` when the entry point itself refused the request — API key, file checks,
# parameters — so no pipeline service was called yet.
ENTRY = "orchestrator"


def service_of(stage: str | None) -> str | None:
    """The `pipeline_name_sequence` name of a stage label (`OCR` -> `extraction`, `GUARDRAILS` -> `guardrails`)."""
    return SERVICE_OF_STAGE.get(stage) if stage else None


def extract_body(
    status_code: int,
    message: str,
    *,
    request_id: str | None,
    data: Mapping[str, Any] | None = None,
    errors: str | None = None,
    guardrails: int | None = None,
    params: Any = None,
    pipeline_last_stage: str | None = None,
) -> dict[str, Any]:
    return {
        **envelope(status_code, message, dict(data) if data is not None else None, request_id, errors=errors),
        "guardrails": guardrails,
        "pipeline_last_stage": pipeline_last_stage,
        "params": params,
    }


def _data(outcome: Mapping[str, Any], threshold: float) -> dict[str, Any]:
    """`data` of a finished request: the result of the last service of its sequence, as it is."""
    last = last_service(outcome.get("sequence"))
    results = outcome.get("results") or {}
    if last == GUARDRAILS:
        return guardrails_data((results.get(STAGE_OCR) or {}).get("guardrails"))
    if last == "extraction":
        return ocr_data(results.get(STAGE_OCR) or {})
    if last == "structuring":
        return structuring_data(results.get(STAGE_STRUCTURING) or {})
    return dict(contract_data(outcome["final"], threshold, outcome.get("columns")))


def extract_response(
    outcome: dict[str, Any], *, request_id: str, params: Any, threshold: float
) -> tuple[int, dict[str, Any]]:
    """(HTTP status, response body) from waiting on the pipeline.

    `guardrails` is 0/1 and means **the document was rejected** (by the guardrails or the structuring rules),
    not a model score; null while the request is still running."""
    pipeline = outcome["pipeline"] or {}
    stage_service = service_of(pipeline.get("stage"))
    status = pipeline.get("status")
    if status == STATUS_REJECTED:
        return 400, extract_body(
            400,
            pipeline["error_message"],
            errors=REJECTED_CODE,
            guardrails=1,
            request_id=request_id,
            params=params,
            pipeline_last_stage=stage_service,
        )
    if status == STATUS_DONE:
        return 200, extract_body(
            200,
            COMPLETED_MESSAGE,
            data=_data(outcome, threshold),
            guardrails=0,
            request_id=request_id,
            params=params,
            pipeline_last_stage=last_service(outcome.get("sequence")),
        )
    if status == STATUS_FAILED:
        stage = pipeline["stage"]
        message = pipeline.get("error_message") or f"{stage} stage failed"
        return 422, extract_body(
            422,
            message,
            errors=f"{stage}_FAILED",
            guardrails=0,
            request_id=request_id,
            params=params,
            pipeline_last_stage=stage_service,
        )
    return 202, extract_body(
        202,
        PROCESSING_MESSAGE,
        request_id=request_id,
        params=params,
        pipeline_last_stage=stage_service,
    )
