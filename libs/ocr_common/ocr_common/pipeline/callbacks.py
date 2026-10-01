"""The two HTTP calls a stage makes when a job finishes: the callback to the orchestrator and the
hand-off to the next stage. `with_retry` is the retry policy of the direct (non-outbox) mode.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ocr_common.clients.remote import RemoteModelClient
from ocr_common.errors import ServiceError

logger = logging.getLogger(__name__)


async def with_retry(call: Callable[[], Awaitable[Any]], attempts: int, delay: float) -> Any:
    """Calls `call` up to `attempts` times, doubling `delay` between tries, on 5xx-class `ServiceError`s only;
    a 4xx is raised at once.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except ServiceError as exc:
            if exc.status_code < 500 or attempt >= attempts:
                raise
            await asyncio.sleep(delay * 2 ** (attempt - 1))


class StageCallback(Protocol):
    """What the pipeline needs from the callback to the orchestrator."""

    async def notify(
        self,
        request_id: str,
        stage: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
        error_code: str | None = None,
        final: bool = False,
    ) -> bool:
        """Direct mode: build and send the callback with retries; returns False when it was skipped or gave up."""
        ...

    async def send(self, body: dict[str, Any]) -> None:
        """Outbox mode: send an already-built callback body once; raises `ServiceError` on failure."""
        ...

    async def aclose(self) -> None:
        """Close the HTTP client."""
        ...


class NextStage(Protocol):
    """What the pipeline needs from the client of the next stage."""

    async def submit(self, payload: dict[str, Any]) -> None:
        """Direct mode: POST the hand-off with retries."""
        ...

    async def send(self, payload: dict[str, Any]) -> None:
        """Outbox mode: POST the hand-off once; raises `ServiceError` on failure."""
        ...

    async def aclose(self) -> None:
        """Close the HTTP client."""
        ...


def stage_callback_body(
    request_id: str,
    stage: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
    error_code: str | None = None,
    final: bool = False,
) -> dict[str, Any]:
    """The per-stage callback body. `error_code` is only present when set: `DOWNSTREAM_VALIDATION_ERROR`
    on a rejection, so a FAILED callback tells a rejected document from a stage that broke. `final: true`
    is only present on the DONE of the stage that ends the request (the last of its
    pipeline_name_sequence), whose `result` is then the request's answer."""
    body: dict[str, Any] = {
        "request_id": request_id,
        "stage": stage,
        "status": status,
        "result": result,
        "error_message": error_message,
    }
    if error_code is not None:
        body["error_code"] = error_code
    if final:
        body["final"] = True
    return body


class OrchestrationCallback:
    """The callback to `ORCHESTRATION_URL`; with no client (URL unset) every call is skipped and logged."""

    def __init__(self, client: RemoteModelClient | None, path: str, *, attempts: int = 3, delay: float = 0.5):
        self._client = client
        self._path = path
        self._attempts = attempts
        self._delay = delay

    async def notify(
        self,
        request_id: str,
        stage: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
        error_code: str | None = None,
        final: bool = False,
    ) -> bool:
        """Send `{request_id, stage, status, result, error_message[, error_code]}` with retries; False when
        skipped or failed."""
        if self._client is None:
            logger.info("callback skipped (ORCHESTRATION_URL not set): %s %s %s", request_id, stage, status)
            return False
        client = self._client
        payload = stage_callback_body(
            request_id, stage, status, result=result, error_message=error_message, error_code=error_code, final=final
        )
        try:
            await with_retry(lambda: client.post_json(self._path, payload), self._attempts, self._delay)
        except ServiceError as exc:
            logger.error("callback failed: %s %s %s: %s", request_id, stage, status, exc.message)
            return False
        return True

    async def send(self, body: dict[str, Any]) -> None:
        """Send one callback body without retries (the outbox relay retries)."""
        if self._client is None:
            logger.info("callback skipped (ORCHESTRATION_URL not set): %s", body.get("request_id"))
            return
        await self._client.post_json(self._path, body)

    async def aclose(self) -> None:
        """Close the HTTP client, if any."""
        if self._client is not None:
            await self._client.aclose()


RESULT_COMPLETED = "completed"
RESULT_FAILED = "failed"
_FINAL_STAGE = "SCORING"


def result_callback_body(stage_body: dict[str, Any]) -> dict[str, Any] | None:
    """The orchestrator's result callback (`ORCHESTRATION_CALLBACK_FORMAT=result`) for a per-stage callback
    body, or None when that stage event is not the end of the request.

    Completed (SCORING `DONE`, `result` = the final result)::

        {"request_id", "status": "completed",
         "result": {"total_slip": int,
                    "slip": [{"page": int, "<field>": {"value": Any, "confidence": float}, ...,
                              "missing_mandatory_fields": [str]}]},
         "guardrails": {...laporan guardrail...}}

    Satu dokumen bisa berisi beberapa slip (tiga bulan dalam satu berkas adalah bentuk yang paling sering
    diunggah), jadi hasilnya array, bukan satu himpunan field datar.

    `confidence` di sini probabilitas mentah 0-1 dari model keyakinan, BUKAN 0/1 seperti di kontrak
    `extract-ocr` (API spec [07]: "callback dan internal: float 0-1"). Field tanpa nilai bernilai null
    dengan confidence 0.0.

    Ended early (`pipeline_name_sequence` berhenti sebelum scoring; DONE tahap itu membawa `final: true`):
    `result` adalah hasil tahap terakhir apa adanya, dan `guardrails` `{}`.

    Failed (tahap mana pun `FAILED`, termasuk penolakan)::

        {"request_id", "status": "failed", "result": null, "guardrails": {},
         "error_code": "<STAGE>_FAILED" | "DOWNSTREAM_VALIDATION_ERROR", "error_message": str}
    """
    request_id, stage, status = stage_body["request_id"], stage_body["stage"], stage_body["status"]
    if status == "FAILED":
        return {
            "request_id": request_id,
            "status": RESULT_FAILED,
            "result": None,
            "guardrails": {},
            "error_code": stage_body.get("error_code") or f"{stage}_FAILED",
            "error_message": stage_body.get("error_message"),
        }
    final = stage_body.get("result")
    if status != "DONE" or not final:
        return None
    if stage != _FINAL_STAGE:
        if not stage_body.get("final"):
            return None
        return {"request_id": request_id, "status": RESULT_COMPLETED, "result": final, "guardrails": {}}
    slips = []
    for slip in final.get("slips") or ():
        values = slip.get("fields") or {}
        scores = slip.get("scores") or {}
        entry: dict[str, Any] = {"page": slip.get("page")}
        for name, value in values.items():
            found = value is not None and str(value).strip() != ""
            entry[name] = {
                "value": value if found else None,
                "confidence": round(float(scores.get(name) or 0.0), 4) if found else 0.0,
            }
        entry["missing_mandatory_fields"] = list(slip.get("missing_mandatory_fields") or ())
        slips.append(entry)
    return {
        "request_id": request_id,
        "status": RESULT_COMPLETED,
        "result": {"total_slip": len(slips), "slip": slips},
        "guardrails": final.get("guardrails") or {},
    }


class ResultCallback:
    """The orchestrator's single result callback (`ORCHESTRATION_CALLBACK_FORMAT=result`): one POST per
    request when it ends, completed by scoring or failed at any stage. It takes the same per-stage events
    as `OrchestrationCallback` (so the pipeline and the outbox are unchanged) and turns them into that
    body; the events that do not end a request (OCR / STRUCTURING `DONE`) are skipped."""

    def __init__(self, client: RemoteModelClient | None, path: str, *, attempts: int = 3, delay: float = 0.5):
        self._client = client
        self._path = path
        self._attempts = attempts
        self._delay = delay

    async def notify(
        self,
        request_id: str,
        stage: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
        error_code: str | None = None,
        final: bool = False,
    ) -> bool:
        """Send the result callback with retries; False when this event is not final, or when it failed."""
        body = result_callback_body(
            stage_callback_body(
                request_id,
                stage,
                status,
                result=result,
                error_message=error_message,
                error_code=error_code,
                final=final,
            )
        )
        if body is None:
            return False
        if self._client is None:
            logger.info("result callback skipped (ORCHESTRATION_URL not set): %s %s", request_id, body["status"])
            return False
        client = self._client
        try:
            await with_retry(lambda: client.post_json(self._path, body), self._attempts, self._delay)
        except ServiceError as exc:
            logger.error("result callback failed: %s %s: %s", request_id, body["status"], exc.message)
            return False
        return True

    async def send(self, body: dict[str, Any]) -> None:
        """Outbox mode: turn a stored per-stage body into the result callback and send it once; nothing for
        an event that does not end the request."""
        result_body = result_callback_body(body)
        if result_body is None:
            return
        if self._client is None:
            logger.info("result callback skipped (ORCHESTRATION_URL not set): %s", body.get("request_id"))
            return
        await self._client.post_json(self._path, result_body)

    async def aclose(self) -> None:
        """Close the HTTP client, if any."""
        if self._client is not None:
            await self._client.aclose()


class NextStageClient:
    """POSTs the hand-off body to the next stage's `/v1/<stage>/jobs`."""

    def __init__(self, client: RemoteModelClient, path: str, *, attempts: int = 3, delay: float = 0.5):
        self._client = client
        self._path = path
        self._attempts = attempts
        self._delay = delay

    async def submit(self, payload: dict[str, Any]) -> None:
        """POST with retries (direct mode)."""
        await with_retry(lambda: self._client.post_json(self._path, payload), self._attempts, self._delay)

    async def send(self, payload: dict[str, Any]) -> None:
        """POST once (outbox mode)."""
        await self._client.post_json(self._path, payload)

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
