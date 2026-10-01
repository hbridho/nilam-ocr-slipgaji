import json
from typing import Any

from ocr_common.clients.remote import RemoteModelClient
from ocr_common.errors import InternalError, ServiceError
from ocr_common.pipeline import with_retry
from ocr_common.testing_endpoints import testing_path

from app.config import Settings

EXTRACTION_JOBS_PATH = "/v1/extraction/jobs"


class ExtractionJobClient:
    def __init__(
        self,
        client: RemoteModelClient,
        *,
        attempts: int = 3,
        delay: float = 0.5,
        jobs_path: str = EXTRACTION_JOBS_PATH,
    ):
        self._client = client
        self._jobs_path = jobs_path
        self._attempts = attempts
        self._delay = delay

    async def submit(
        self,
        request_id: str,
        document_type: str,
        filename: str,
        content_type: str | None,
        content: bytes,
        *,
        file_url: str | None = None,
        sequence: list[str] | None = None,
        guardrail_thresholds: dict[str, Any] | None = None,
        columns: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Serahkan dokumen ke tahap OCR. Ketika permintaan datang sebagai `file_url`, URL itulah yang
        diteruskan alih-alih byte-nya: service OCR mengunduhnya sendiri, dan job yang ditinggal proses
        mati bisa dijalankan ulang dari URL yang tersimpan bersama job.

        Guardrail tidak dipanggil dari sini — tahap OCR yang memanggil ketiganya setelah teksnya ada,
        karena modelnya membaca teks OCR. Yang diteruskan: urutan service dan ambang permintaan ini,
        sudah diperiksa dan dinormalkan."""
        fields: dict[str, Any] = {"request_id": request_id, "document_type": document_type}
        if sequence:
            fields["pipeline_name_sequence"] = json.dumps(sequence)
        if guardrail_thresholds:
            fields["guardrails_confidence_threshold"] = json.dumps(guardrail_thresholds)
        if columns:
            fields["column_confidence_threshold"] = json.dumps(columns)
        if file_url:
            call = lambda: self._client.post_form(self._jobs_path, data={**fields, "file_url": file_url})  # noqa: E731
        else:
            call = lambda: self._client.post_multipart(  # noqa: E731
                self._jobs_path,
                filename=filename or "upload",
                content=content,
                content_type=content_type or "application/octet-stream",
                data=fields,
            )
        try:
            body = await with_retry(call, self._attempts, self._delay)
        except ServiceError as exc:
            # API spec [07]: an error from (or reaching) the OCR stage names it in `pipeline_last_stage`.
            exc.service = "extraction"  # ty: ignore[unresolved-attribute]
            raise
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            raise InternalError(f"{self._client.name} returned an unexpected response")
        return body["data"]

    async def aclose(self) -> None:
        await self._client.aclose()


def build_extraction_client(settings: Settings, *, testing: bool = False) -> ExtractionJobClient:
    """The client to the OCR stage; `testing=True` submits to its `-test` endpoint (TESTING_ENDPOINTS)."""
    client = RemoteModelClient(
        settings.extraction_service_url,
        settings.extraction_timeout_seconds,
        name="extraction service (testing)" if testing else "extraction service",
        headers={"X-API-Key": settings.extraction_api_key or settings.api_key},
        passthrough_client_errors=True,
    )
    return ExtractionJobClient(
        client,
        attempts=settings.pipeline_retry_attempts,
        delay=settings.pipeline_retry_delay_seconds,
        jobs_path=testing_path(EXTRACTION_JOBS_PATH) if testing else EXTRACTION_JOBS_PATH,
    )
