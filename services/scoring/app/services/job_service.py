from collections.abc import Callable, Mapping
from typing import Any, cast

from starlette.concurrency import run_in_threadpool

from ocr_common.errors import BadRequest, UnprocessableEntity
from ocr_common.pipeline import StagePipeline, Work
from ocr_common.pipeline.results import StageResults, load_upstream
from ocr_common.slip_gaji import DOCUMENT_TYPE, contract_data, final_result
from ocr_common.types import FinalResult, ScoringResult

from app.services.confidence_service import ConfidenceService

Final = Callable[[Mapping[str, Any]], FinalResult]


class ScoringJobService:
    """Tahap terakhir pipeline: skor keyakinan 0-1 per field, lalu hasil akhir.

    Scoring selalu tahap terakhir, jadi job ini selalu mengakhiri permintaan: callback DONE-nya membawa
    hasil akhir (probabilitas 0-1 per field), dan baris hasil Orkestrasi pusat membawa `data` kontrak
    `extract-ocr` (confidence 0/1) menurut `column_confidence_threshold` permintaan ini."""

    def __init__(
        self,
        pipeline: StagePipeline,
        confidence: ConfidenceService,
        confidence_threshold: float = 0.5,
        *,
        results: StageResults | None = None,
    ):
        self._pipeline = pipeline
        self._confidence = confidence
        self._confidence_threshold = confidence_threshold
        self._results = results

    async def submit(
        self,
        request_id: str,
        document_type: str,
        guardrails: dict[str, Any] | None,
        ocr: dict[str, Any] | None,
        structuring: dict[str, Any] | None,
        *,
        sequence: list[str] | None = None,
        columns: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        if structuring is None and self._results is None:
            raise UnprocessableEntity(
                "structuring tidak ada: permintaan ini merujuk hasil structuring lewat request_id, tetapi service "
                "ini tidak punya DATABASE_URL untuk membaca structuring_results",
            )
        work, final = self._spec(request_id, document_type, guardrails, structuring, columns)
        return await self._pipeline.submit(
            request_id,
            work,
            callback_result=final,
            outcome_data=self._outcome(final, columns),
            input={
                "document_type": document_type,
                "guardrails": guardrails,
                "pipeline_name_sequence": sequence,
                "column_confidence_threshold": columns,
            },
        )

    async def resume(self, request_id: str, input: dict[str, Any] | None) -> None:
        """Jalankan lagi job yang ditinggal proses mati dalam keadaan `PROCESSING`: hasil structuring
        dan OCR dibaca dari basis data, sisanya dari `input` yang disimpan saat job diklaim."""
        input = input or {}
        columns = input.get("column_confidence_threshold")
        work, final = self._spec(
            request_id, input.get("document_type") or DOCUMENT_TYPE, input.get("guardrails"), None, columns
        )
        await self._pipeline.resume(request_id, work, callback_result=final, outcome_data=self._outcome(final, columns))

    async def get(self, request_id: str) -> dict[str, Any]:
        return await self._pipeline.get(request_id)

    def _outcome(self, final: Final, columns: dict[str, float] | None) -> Callable[[Mapping[str, Any]], Any]:
        return lambda scoring: contract_data(final(scoring), self._confidence_threshold, columns)

    def _spec(
        self,
        request_id: str,
        document_type: str,
        guardrails: dict[str, Any] | None,
        structuring: dict[str, Any] | None,
        columns: dict[str, float] | None,
    ) -> tuple[Work, Final]:
        chain: dict[str, Any] = {}

        async def work() -> ScoringResult:
            if document_type != DOCUMENT_TYPE:
                raise BadRequest(f"Unsupported document_type: {document_type}. Supported: ['{DOCUMENT_TYPE}']")
            chain["structuring"] = (
                structuring
                if structuring is not None
                else await load_upstream(self._results, "structuring", request_id)
            )
            result = cast(ScoringResult, await run_in_threadpool(self._confidence.score, chain["structuring"]))
            return ScoringResult(**result, column_confidence_threshold=columns)

        def final(scoring: Mapping[str, Any]) -> FinalResult:
            return final_result(document_type, guardrails, chain["structuring"], scoring)

        return work, final
