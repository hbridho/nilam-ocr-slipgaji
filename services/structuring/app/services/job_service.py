from collections.abc import Mapping
from typing import Any

from ocr_common.errors import UnprocessableEntity
from ocr_common.pipeline import STRUCTURING, HandoffPayload, StagePipeline, Work, chain
from ocr_common.pipeline.results import StageResults, load_upstream
from ocr_common.slip_gaji import DOCUMENT_TYPE, structuring_data
from ocr_common.types import StructuringResult

from app.services.structuring_service import StructuringService

Handoff = HandoffPayload


def _rejection(structuring: Mapping[str, Any]) -> str | None:
    """Alasan tahap ini menolak dokumen; None kalau tidak menolak.

    Untuk slip gaji penolakan biasanya terjadi di guardrail (langkah tahap OCR, karena modelnya
    membaca teks). Kait ini tetap ada supaya aturan structuring bisa menolak tanpa mengubah
    pipeline — mis. kelak "dokumen ini slip gaji, tapi tidak ada satu pun field wajib yang
    terbaca"."""
    return structuring.get("reject_reason") or None


class StructuringJobService:
    def __init__(
        self,
        pipeline: StagePipeline,
        structuring: StructuringService,
        *,
        results: StageResults | None = None,
        handoff_by_reference: bool = False,
    ):
        self._pipeline = pipeline
        self._structuring = structuring
        self._results = results
        self._handoff_by_reference = handoff_by_reference

    async def submit(
        self,
        request_id: str,
        document_type: str,
        guardrails: dict[str, Any] | None,
        ocr: dict[str, Any] | None,
        *,
        sequence: list[str] | None = None,
        columns: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        if ocr is None and self._results is None:
            raise UnprocessableEntity(
                "ocr is missing: the request refers to the OCR result by request_id, but this service has no "
                "DATABASE_URL to read nilam_ocr_results from",
            )
        work, handoff = self._spec(request_id, document_type, guardrails, ocr, sequence, columns)
        return await self._pipeline.submit(
            request_id,
            work,
            rejection=_rejection,
            input={
                "document_type": document_type,
                "guardrails": guardrails,
                "pipeline_name_sequence": sequence,
                "column_confidence_threshold": columns,
            },
            **self._continuation(sequence, handoff),
        )

    async def resume(self, request_id: str, input: dict[str, Any] | None) -> None:
        """Run again a job a dead process left `PROCESSING`: the OCR result is read from the database,
        the rest comes from the `input` stored when the job was claimed."""
        input = input or {}
        sequence = input.get("pipeline_name_sequence")
        work, handoff = self._spec(
            request_id,
            input.get("document_type") or DOCUMENT_TYPE,
            input.get("guardrails"),
            None,
            sequence,
            input.get("column_confidence_threshold"),
        )
        await self._pipeline.resume(request_id, work, rejection=_rejection, **self._continuation(sequence, handoff))

    async def get(self, request_id: str) -> dict[str, Any]:
        return await self._pipeline.get(request_id)

    @staticmethod
    def _continuation(sequence: list[str] | None, handoff: Handoff) -> dict[str, Any]:
        """Hand on to scoring, or — when `structuring` ends the sequence — end the request here with the
        structuring result as its answer."""
        following = chain(sequence, STRUCTURING, handoff)
        if following["next_stage"] is None:
            return {"callback_result": structuring_data, "outcome_data": structuring_data}
        return following

    def _spec(
        self,
        request_id: str,
        document_type: str,
        guardrails: dict[str, Any] | None,
        ocr: dict[str, Any] | None,
        sequence: list[str] | None,
        columns: dict[str, float] | None,
    ) -> tuple[Work, Handoff]:
        state: dict[str, Any] = {}

        async def work() -> StructuringResult:
            state["ocr"] = ocr if ocr is not None else await load_upstream(self._results, "ocr", request_id)
            pages = StructuringService.pages_from_ocr(state["ocr"])
            return await self._structuring.run(pages)

        def handoff(structuring: Mapping[str, Any]) -> dict[str, Any]:
            body: dict[str, Any] = {
                "request_id": request_id,
                "document_type": document_type,
                "guardrails": guardrails,
                "pipeline_name_sequence": sequence,
                "column_confidence_threshold": columns,
            }
            if not self._handoff_by_reference:
                body.update(ocr=state["ocr"], structuring=dict(structuring))
            return body

        return work, handoff
