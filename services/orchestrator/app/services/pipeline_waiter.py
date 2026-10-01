import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ocr_common.errors import InternalError, ServiceError
from ocr_common.pipeline import (
    STAGE_GUARDRAILS,
    STAGE_OCR,
    STAGE_OF,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PROCESSING,
    last_service,
)

logger = logging.getLogger(__name__)

# The pipeline stopped because a stage rejected the document (a rejecting check of the structuring
# rules: `reject_reason` in its result). Final, like FAILED, but answered as a 400.
STATUS_REJECTED = "REJECTED"


class StageStatus(Protocol):
    stage: str

    async def get(self, request_id: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class WaitOutcome:
    stage: str
    status: str
    error_message: str | None = None
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The request's pipeline_name_sequence (None: the full pipeline), as stored with its OCR job.
    sequence: list[str] | None = None


class PipelineWait(Protocol):
    async def wait(self, request_id: str, timeout: float, *, sequence: list[str] | None = None) -> WaitOutcome: ...

    async def snapshot(self, request_id: str) -> WaitOutcome | None: ...


def _stages_for(stages: tuple[StageStatus, ...], sequence: list[str] | None) -> tuple[StageStatus, ...]:
    """The stage jobs the request has: up to the stage of its last service. `guardrails` and `extraction`
    both live in the OCR job, so `["guardrails"]` reads that one job only."""
    last = STAGE_OF[last_service(sequence)]
    if last == STAGE_GUARDRAILS:
        last = STAGE_OCR
    names = [stage.stage for stage in stages]
    return stages[: names.index(last) + 1] if last in names else stages


class PipelineWaiter:
    """Reads the jobs of the stages, in pipeline order, to tell where a request is."""

    def __init__(self, stages: Sequence[StageStatus], *, poll_interval: float):
        self._stages = tuple(stages)
        self._poll_interval = poll_interval

    async def wait(self, request_id: str, timeout: float, *, sequence: list[str] | None = None) -> WaitOutcome:
        """Polls until the pipeline ends (DONE, FAILED or rejected) or `timeout` runs out (PROCESSING)."""
        stages = _stages_for(self._stages, sequence)
        results: dict[str, dict[str, Any]] = {}
        current = stages[0].stage
        if timeout <= 0:
            return WaitOutcome(current, STATUS_PROCESSING, results=results, sequence=sequence)
        try:
            async with asyncio.timeout(timeout):
                for stage in stages:
                    current = stage.stage
                    record = await self._until_finished(stage, request_id)
                    ended = _ended(current, record, results, sequence)
                    if ended is not None:
                        return ended
        except TimeoutError:
            return WaitOutcome(current, STATUS_PROCESSING, results=results, sequence=sequence)
        return WaitOutcome(current, STATUS_DONE, results=results, sequence=sequence)

    async def snapshot(self, request_id: str) -> WaitOutcome | None:
        """Where the request is now, reading each stage at most once; None when the first stage has no job
        for it (the request never entered the pipeline).

        The request's pipeline_name_sequence is read from its OCR job, so a GET stops at the same stage
        as the POST did. A stage that cannot be read raises (503/504/500) instead of being reported as
        still running. A hand-off that failed for good (retries exhausted, or an outbox dead letter)
        leaves the next stage without a job, so it reads as PROCESSING: the FAILED callback and the
        orchestrator's tables hold that final state."""
        results: dict[str, dict[str, Any]] = {}
        stages = self._stages
        sequence: list[str] | None = None
        for index, stage in enumerate(stages):
            record = await stage.get(request_id)
            if record is None:
                if index == 0:
                    return None
                return WaitOutcome(stage.stage, STATUS_PROCESSING, results=results, sequence=sequence)
            if index == 0:
                sequence = record.get("pipeline_name_sequence") or None
                stages = _stages_for(self._stages, sequence)
            status = record.get("status")
            if status not in (STATUS_PROCESSING, STATUS_DONE, STATUS_FAILED):
                raise InternalError(f"{stage.stage} job has an unexpected status: {status}")
            if status == STATUS_PROCESSING:
                return WaitOutcome(stage.stage, STATUS_PROCESSING, results=results, sequence=sequence)
            ended = _ended(stage.stage, record, results, sequence)
            if ended is not None:
                return ended
            if index + 1 >= len(stages):
                break
        return WaitOutcome(stages[-1].stage, STATUS_DONE, results=results, sequence=sequence)

    async def _until_finished(self, stage: StageStatus, request_id: str) -> dict[str, Any]:
        while True:
            try:
                record = await stage.get(request_id)
            except ServiceError as exc:
                logger.warning("waiting for %s job %s: %s", stage.stage, request_id, exc.message)
                record = None
            if record is not None and record.get("status") in (STATUS_DONE, STATUS_FAILED):
                return record
            await asyncio.sleep(self._poll_interval)


def _ended(
    stage: str, record: dict[str, Any], results: dict[str, dict[str, Any]], sequence: list[str] | None
) -> WaitOutcome | None:
    """The outcome when this finished job (DONE or FAILED) ends the pipeline, else None after keeping its
    result in `results`. Shared by `wait` and `snapshot`, so both read a job the same way."""
    if record["status"] == STATUS_FAILED:
        return WaitOutcome(stage, STATUS_FAILED, record.get("error_message"), results, sequence)
    results[stage] = record.get("result") or {}
    reject_reason = results[stage].get("reject_reason")
    if reject_reason:
        # The OCR job's rejection comes from the guardrails it ran after reading the document.
        rejected_at = STAGE_GUARDRAILS if stage == STAGE_OCR else stage
        return WaitOutcome(rejected_at, STATUS_REJECTED, reject_reason, results, sequence)
    return None
