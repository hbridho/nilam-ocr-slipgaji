import asyncio
from collections.abc import Mapping
from typing import Any

from ocr_common.clients.fetch_url import STRICT_URL_POLICY, FetchUrlError, UrlPolicy, fetch
from ocr_common.errors import BadRequest
from ocr_common.pipeline import (
    EXTRACTION,
    GUARDRAILS,
    HandoffPayload,
    StagePipeline,
    Work,
    chain,
    last_service,
    runs_guardrails,
)
from ocr_common.simulation import simulated_delay_seconds
from ocr_common.slip_gaji import DOCUMENT_TYPE, guardrails_data, ocr_data
from ocr_common.types import OcrResult

from app.services.extraction_service import ExtractionService
from app.services.guardrails_log import SqlGuardrailsLog

UploadedFile = tuple[bytes, str, str | None]
Source = UploadedFile | str
Handoff = HandoffPayload

INLINE_UPLOAD_GONE = (
    "job ini tidak bisa dijalankan ulang: dokumennya diunggah inline dan ikut hilang bersama proses yang mati; "
    "kirim permintaannya lagi (dokumen yang dikirim sebagai file_url bisa diambil ulang)"
)


def _ended_at_guardrails(ocr: Mapping[str, Any]) -> dict[str, Any]:
    return guardrails_data(ocr.get("guardrails"))


def _ended_at_extraction(ocr: Mapping[str, Any]) -> dict[str, Any]:
    return ocr_data(ocr)


class ExtractionJobService:
    """The OCR job: read the document, judge it with the guardrails when the sequence asks for them, then
    hand it to structuring — or, when the sequence ends here (`["guardrails"]`, `[..., "extraction"]`),
    end the request with this stage's result as its answer."""

    def __init__(
        self,
        pipeline: StagePipeline,
        extraction: ExtractionService,
        max_upload_bytes: int,
        url_policy: UrlPolicy = STRICT_URL_POLICY,
        *,
        simulate_delay: bool = False,
        handoff_by_reference: bool = False,
        guardrails_log: SqlGuardrailsLog | None = None,
    ):
        self._pipeline = pipeline
        self._extraction = extraction
        self._max_upload_bytes = max_upload_bytes
        self._url_policy = url_policy
        self._simulate_delay = simulate_delay
        self._handoff_by_reference = handoff_by_reference
        self._guardrails_log = guardrails_log

    async def submit(
        self,
        request_id: str,
        document_type: str,
        source: Source,
        *,
        sequence: list[str] | None = None,
        guardrail_thresholds: dict[str, Any] | None = None,
        columns: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        work, handoff = self._spec(request_id, document_type, source, sequence, guardrail_thresholds, columns)
        return await self._pipeline.submit(
            request_id,
            work,
            # Guardrail dinilai DI tahap ini (modelnya membaca teks OCR), jadi dokumen yang ditahan berhenti
            # di sini: jobnya tetap DONE dan hasil OCR-nya tersimpan, tetapi `reject_reason` membuat pipeline
            # tidak diteruskan.
            rejection=lambda result: result.get("reject_reason"),
            input={
                "document_type": document_type,
                "file_url": source if isinstance(source, str) else None,
                "pipeline_name_sequence": sequence,
                "guardrails_confidence_threshold": guardrail_thresholds,
                "column_confidence_threshold": columns,
            },
            **self._continuation(sequence, handoff),
        )

    async def resume(self, request_id: str, input: dict[str, Any] | None) -> None:
        """Jalankan lagi job yang ditinggal proses mati dalam keadaan `PROCESSING`. Hanya dokumen
        yang dikirim sebagai `file_url` yang bisa diambil ulang; unggahan inline sudah hilang, jadi
        job itu digagalkan dengan pesan yang meminta pengiriman ulang."""
        input = input or {}
        file_url = input.get("file_url")
        if not file_url:

            async def gone() -> dict[str, Any]:
                raise BadRequest(INLINE_UPLOAD_GONE)

            await self._pipeline.resume(request_id, gone)
            return
        sequence = input.get("pipeline_name_sequence")
        work, handoff = self._spec(
            request_id,
            input.get("document_type") or DOCUMENT_TYPE,
            file_url,
            sequence,
            input.get("guardrails_confidence_threshold"),
            input.get("column_confidence_threshold"),
        )
        await self._pipeline.resume(
            request_id,
            work,
            rejection=lambda result: result.get("reject_reason"),
            **self._continuation(sequence, handoff),
        )

    async def get(self, request_id: str) -> dict[str, Any]:
        return await self._pipeline.get(request_id)

    @staticmethod
    def _continuation(sequence: list[str] | None, handoff: Handoff) -> dict[str, Any]:
        last = last_service(sequence)
        if last == GUARDRAILS:
            return {"callback_result": _ended_at_guardrails, "outcome_data": _ended_at_guardrails}
        following = chain(sequence, EXTRACTION, handoff)
        if following["next_stage"] is None:
            return {"callback_result": _ended_at_extraction, "outcome_data": _ended_at_extraction}
        return following

    def _spec(
        self,
        request_id: str,
        document_type: str,
        source: Source,
        sequence: list[str] | None,
        guardrail_thresholds: dict[str, Any] | None,
        columns: dict[str, float] | None,
    ) -> tuple[Work, Handoff]:
        async def work() -> OcrResult:
            content, filename, content_type = await self._load(source)
            delay = simulated_delay_seconds(filename, enabled=self._simulate_delay)
            if delay:
                await asyncio.sleep(delay)
            result = await self._extraction.extract(
                filename,
                content_type,
                content,
                request_id=request_id,
                run_guardrails=runs_guardrails(sequence),
                guardrail_thresholds=guardrail_thresholds,
            )
            # Jawaban tiap guardrail ke nilam_guardrails_results, juga dokumen yang ditolak (best-effort).
            if self._guardrails_log is not None and result.get("guardrails"):
                await self._guardrails_log.record(request_id, result["guardrails"], sequence=sequence)
            return result  # ty: ignore[invalid-return-type]

        def handoff(ocr: Mapping[str, Any]) -> dict[str, Any]:
            body: dict[str, Any] = {
                "request_id": request_id,
                "document_type": document_type,
                "guardrails": ocr.get("guardrails"),
                "pipeline_name_sequence": sequence,
                "column_confidence_threshold": columns,
            }
            if not self._handoff_by_reference:
                body["ocr"] = {key: value for key, value in ocr.items() if key != "guardrails"}
            return body

        return work, handoff

    async def _load(self, source: Source) -> UploadedFile:
        if not isinstance(source, str):
            return source
        try:
            return await fetch(source, limit=self._max_upload_bytes, policy=self._url_policy)
        except FetchUrlError as exc:
            raise BadRequest(str(exc)) from exc
