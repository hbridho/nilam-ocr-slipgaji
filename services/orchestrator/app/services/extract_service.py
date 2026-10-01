import logging
import time
from typing import Any

from ocr_common.errors import NotFound
from ocr_common.pipeline import STAGE_OCR, STAGE_SCORING, STAGE_STRUCTURING, STATUS_DONE, STATUS_PROCESSING
from ocr_common.slip_gaji import DOCUMENT_TYPE, final_result

from app.clients.extraction import ExtractionJobClient
from app.config import Settings
from app.services.document_checks import check_document
from app.services.pipeline_waiter import PipelineWait, WaitOutcome

logger = logging.getLogger(__name__)


class ExtractOcrService:
    """Yang berjalan di balik `extract-ocr`: pemeriksaan berkas, penyerahan ke tahap OCR, dan
    penantian sampai service terakhir `pipeline_name_sequence`.

    Guardrail TIDAK dipanggil dari sini. Modelnya membaca teks OCR, jadi tahap OCR yang memanggil
    ketiga service guardrail setelah membaca dokumen; putusannya kembali ke sini sebagai penolakan
    (`REJECTED` di tahap `GUARDRAILS`) atau sebagai bagian dari hasil akhir.
    """

    def __init__(self, extraction: ExtractionJobClient, waiter: PipelineWait, settings: Settings):
        self._extraction = extraction
        self._waiter = waiter
        self._settings = settings

    async def submit(
        self,
        request_id: str,
        document_type: str,
        filename: str,
        content_type: str | None,
        content: bytes,
        *,
        received_at: float | None = None,
        file_url: str | None = None,
        sequence: list[str] | None = None,
        guardrail_thresholds: dict[str, Any] | None = None,
        columns: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Periksa berkasnya (jenis, kosong, `MAX_UPLOAD_BYTES`, `MAX_DOCUMENT_PAGES`) sebelum siapa pun
        melihatnya, serahkan ke tahap OCR, lalu tunggu pipeline selama sisa `PIPELINE_WAIT_SECONDS`
        sejak `received_at`."""
        started = time.monotonic() if received_at is None else received_at
        check_document(content_type, content, self._settings)
        job = await self._extraction.submit(
            request_id,
            document_type,
            filename,
            content_type,
            content,
            file_url=file_url,
            sequence=sequence,
            guardrail_thresholds=guardrail_thresholds,
            columns=columns,
        )
        wait_seconds = self._settings.pipeline_wait_seconds
        if wait_seconds <= 0:
            outcome = WaitOutcome(STAGE_OCR, STATUS_PROCESSING, sequence=sequence)
        else:
            remaining = wait_seconds - (time.monotonic() - started)
            outcome = await self._waiter.wait(request_id, remaining, sequence=sequence)
        return {"job": job, **_pipeline(document_type, outcome, columns)}

    async def status(self, request_id: str) -> dict[str, Any]:
        """Di mana permintaan ini sekarang, dibaca dari tahap-tahapnya tanpa menunggu; 404 ketika ia
        tidak pernah masuk pipeline."""
        outcome = await self._waiter.snapshot(request_id)
        if outcome is None:
            raise NotFound(f"No request found for request_id {request_id}")
        scoring = outcome.results.get(STAGE_SCORING) or {}
        return _pipeline(DOCUMENT_TYPE, outcome, scoring.get("column_confidence_threshold"))


def _pipeline(document_type: str, outcome: WaitOutcome, columns: dict[str, float] | None) -> dict[str, Any]:
    final = None
    if outcome.status == STATUS_DONE and STAGE_SCORING in outcome.results:
        # Laporan guardrail diambil dari hasil tahap OCR — di situlah ia dihasilkan.
        guardrails = (outcome.results.get(STAGE_OCR) or {}).get("guardrails")
        final = final_result(
            document_type, guardrails, outcome.results[STAGE_STRUCTURING], outcome.results[STAGE_SCORING]
        )
    return {
        "pipeline": {"stage": outcome.stage, "status": outcome.status, "error_message": outcome.error_message},
        "sequence": outcome.sequence,
        "results": outcome.results,
        "final": final,
        "columns": columns,
    }
