"""Keeps the answer of each guardrail in `nilam_guardrails_results`: one row per guardrail (blank, blur, identity)
every time the OCR job judges a document, the rejected documents included. They never reach a stage table, so
without this a rejection would only be found inside the OCR result's JSON.

A guardrail switched off (`GUARDRAIL_<NAME>_ENABLED=false`) or not answering (fail-open) still gets its row, with
`verdict` `skipped` or `unavailable`, so every judged request has its three rows and the counts add up.

Best-effort: an insert that fails or takes longer than `timeout` is logged and the job goes on, because the OCR
result must not be lost to the audit trail."""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import MetaData

from ocr_common.clients.guardrails import CHECKS, accept_probability, accept_threshold
from ocr_common.pipeline.database import get_engine
from ocr_common.pipeline.tables import guardrails_results_table

logger = logging.getLogger(__name__)

SOURCE_REQUEST = "request"  # the central orchestrator sent the threshold with the request
SOURCE_SERVICE = "service"  # the guardrail used its own
VERDICT_SKIPPED = "skipped"
VERDICT_UNAVAILABLE = "unavailable"


def guardrail_rows(
    request_id: str, report: Mapping[str, Any], *, sequence: Sequence[str] | None, now: datetime
) -> list[dict[str, Any]]:
    """The rows of one merged guardrails report (`ocr_common.clients.guardrails.merge`), one per guardrail."""
    checks = report.get("checks") or {}
    skipped = set(report.get("skipped") or ())
    unavailable = set(report.get("unavailable") or ())
    common = {
        "request_id": request_id,
        "n_pages": (report.get("document") or {}).get("n_pages"),
        "pipeline_name_sequence": list(sequence) if sequence else None,
        "created_at": now,
        "ds": now.strftime("%Y%m%d"),
    }
    rows = []
    for name in CHECKS:
        answer = checks.get(name)
        if answer is not None:
            confidence = accept_probability(name, answer)
            rows.append(
                {
                    **common,
                    "guardrail": name,
                    "passed": bool(answer.get("passed")),
                    "verdict": answer.get("verdict"),
                    "confidence": None if confidence is None else round(confidence, 4),
                    "threshold": accept_threshold(name, answer),
                    "threshold_source": SOURCE_REQUEST
                    if answer.get("threshold_source") == SOURCE_REQUEST
                    else SOURCE_SERVICE,
                    "reason": answer.get("reason"),
                    "report": dict(answer),
                }
            )
        elif name in skipped or name in unavailable:
            verdict = VERDICT_UNAVAILABLE if name in unavailable else VERDICT_SKIPPED
            rows.append(
                {
                    **common,
                    "guardrail": name,
                    "passed": True,
                    "verdict": verdict,
                    "confidence": None,
                    "threshold": None,
                    "threshold_source": SOURCE_SERVICE,
                    "reason": None,
                    "report": {"check": name, "verdict": verdict},
                }
            )
    return rows


class SqlGuardrailsLog:
    def __init__(self, database_url: str, *, table_prefix: str = "", timeout: float = 5.0):
        self._url = database_url
        self._table = guardrails_results_table(MetaData(), table_prefix)
        self._timeout = timeout

    async def record(self, request_id: str, report: Mapping[str, Any], *, sequence: Sequence[str] | None) -> None:
        rows = guardrail_rows(request_id, report, sequence=sequence, now=datetime.now(UTC))
        if not rows:
            return
        try:
            await asyncio.wait_for(self._insert(rows), self._timeout)
        except Exception:  # noqa: BLE001 - best-effort, see the module docstring
            logger.exception("guardrail results of %s not recorded", request_id)

    async def _insert(self, rows: list[dict[str, Any]]) -> None:
        async with get_engine(self._url).begin() as conn:
            await conn.execute(self._table.insert(), rows)
