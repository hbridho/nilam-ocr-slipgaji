"""The three slip gaji guardrails, called at the same time, merged into one report.

    guardrail-blank      POST /v1/guardrail/blank/check      no text?            text-length rule
    guardrail-blur       POST /v1/guardrail/blur/check       too broken to read? 6 OCR-quality features
    guardrail-identity   POST /v1/guardrail/identity/check   a slip gaji at all? TF-IDF + layout

They are three services on purpose, and each one can be gone without the others going down with it:

* switched off (`GUARDRAIL_<NAME>_ENABLED=false`, or no URL): the check is not asked and is listed in
  `skipped`; the other checks still decide.
* switched on but not answering (down, timed out, answering off-contract): with `GUARDRAILS_FAIL_OPEN=true`
  (default) the check is listed in `unavailable` and the other checks still decide; with `false` the error
  is raised, and the OCR job fails (503/504) rather than letting a document through unjudged.

The answers are merged with the precedence blank > blur > identity: on a blank page the blur model also
says "blur" and the identity model guesses — what the user must be told is "this page is empty".

Every probability is 0-1. The request's own thresholds (`guardrails_confidence_threshold`,
`guardrails_tendency`, see `ocr_common.thresholds`) are applied here, to the probabilities the services
return, so the three services stay stateless and need no per-request parameters.
"""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ocr_common.clients.remote import RemoteModelClient
from ocr_common.errors import InternalError, ServiceError
from ocr_common.thresholds import passes

logger = logging.getLogger(__name__)

BLANK = "blank"
BLUR = "blur"
IDENTITY = "identity"
CHECKS: tuple[str, ...] = (BLANK, BLUR, IDENTITY)  # also the precedence of their rejections
PATHS = {name: f"/v1/guardrail/{name}/check" for name in CHECKS}

VERDICT_PASSED = "slip_gaji"
VERDICT_SKIPPED = "skipped"
REASON_WRONG_DOCUMENT = "Dokumen ini bukan slip gaji. Mohon unggah slip gaji."
REASON_BLUR = "Dokumen terlalu buram untuk dibaca. Mohon foto ulang dengan lebih jelas."


@dataclass(frozen=True)
class GuardrailEndpoint:
    """One guardrail service: None `client` means it is switched off."""

    name: str
    client: RemoteModelClient | None


def _accept_probability(name: str, report: Mapping[str, Any]) -> float | None:
    """P(this check lets the document through), from the service's own report."""
    if name == IDENTITY:
        value = report.get("proba_slip_gaji")
    elif name == BLUR:
        broken = report.get("p_broken")
        value = None if broken is None else 1.0 - float(broken)
    else:
        return None
    return None if value is None else float(value)


def apply_threshold(name: str, report: dict[str, Any], threshold: Mapping[str, Any] | None) -> dict[str, Any]:
    """The report with the request's threshold applied (verdict, passed, reason, threshold fields)."""
    accept = _accept_probability(name, report)
    if threshold is None or accept is None:
        return report
    passed = passes(accept, threshold)
    out = {
        **report,
        "passed": passed,
        "threshold": float(threshold["value"]),
        "threshold_target": threshold["target"],
        "threshold_source": "request",
    }
    if name == IDENTITY:
        out.update(
            verdict="slip_gaji" if passed else "bukan_slip_gaji", reason=None if passed else REASON_WRONG_DOCUMENT
        )
    elif name == BLUR:
        out.update(verdict="ok" if passed else "blur", reason=None if passed else REASON_BLUR)
    return out


def _rejection_confidence(name: str, report: Mapping[str, Any]) -> float | None:
    if name == BLANK:
        return 1.0
    accept = _accept_probability(name, report)
    return None if accept is None else round(1.0 - accept, 4)


def merge(
    reports: Mapping[str, dict[str, Any] | None],
    *,
    skipped: list[str],
    unavailable: list[str],
    n_pages: int,
) -> dict[str, Any]:
    """One document verdict from the checks that answered, in the API spec [07] report shape."""
    identity = reports.get(IDENTITY)
    identity_threshold = None
    identity_target = None
    if identity is not None:
        identity_threshold = identity.get("threshold", identity.get("reject_threshold"))
        identity_target = identity.get("threshold_target", "accept")

    for name in CHECKS:
        report = reports.get(name)
        if report is not None and not report.get("passed", True):
            return {
                "passed": False,
                "reason": report.get("reason"),
                "verdict": report.get("verdict"),
                "rejected_by": name,
                "document": {
                    "verdict": "reject",
                    "confidence": _rejection_confidence(name, report),
                    "n_pages": n_pages,
                    "threshold": report.get("threshold", report.get("reject_threshold", report.get("max_chars"))),
                    "threshold_target": report.get("threshold_target", "accept" if name == IDENTITY else "reject"),
                },
                "checks": dict(reports),
                "skipped": skipped,
                "unavailable": unavailable,
                "pages": [],
            }
    judged = [name for name in CHECKS if reports.get(name) is not None]
    return {
        "passed": True,
        "reason": None,
        "verdict": VERDICT_PASSED if judged else VERDICT_SKIPPED,
        "rejected_by": None,
        "document": {
            "verdict": "accepted",
            "confidence": None if identity is None else identity.get("proba_slip_gaji"),
            "n_pages": n_pages,
            "threshold": identity_threshold,
            "threshold_target": identity_target,
        },
        "checks": dict(reports),
        "skipped": skipped,
        "unavailable": unavailable,
        "pages": [],
    }


def skipped_report(reason: str, *, n_pages: int = 0) -> dict[str, Any]:
    """The report of a request whose sequence leaves the guardrails out: nothing judged it, and it says so."""
    report = merge({}, skipped=list(CHECKS), unavailable=[], n_pages=n_pages)
    return {**report, "skipped_reason": reason}


class GuardrailsFanout:
    """Calls the switched-on guardrails concurrently and merges their answers."""

    def __init__(self, endpoints: list[GuardrailEndpoint], *, fail_open: bool = True):
        self._endpoints = {endpoint.name: endpoint for endpoint in endpoints}
        self._fail_open = fail_open

    @property
    def enabled(self) -> list[str]:
        return [name for name in CHECKS if (self._endpoints.get(name) and self._endpoints[name].client)]

    async def check(
        self,
        request_id: str,
        text: str,
        confidence: Mapping[str, Any] | None,
        *,
        n_pages: int,
        thresholds: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        thresholds = thresholds or {}
        body = {"request_id": request_id, "text": text, "confidence": dict(confidence or {})}
        active = self.enabled
        skipped = [name for name in CHECKS if name not in active]
        answers = await asyncio.gather(*(self._ask(name, body) for name in active), return_exceptions=True)

        reports: dict[str, dict[str, Any] | None] = dict.fromkeys(CHECKS)
        unavailable: list[str] = []
        for name, answer in zip(active, answers, strict=True):
            if isinstance(answer, BaseException):
                if not self._fail_open or not isinstance(answer, ServiceError):
                    raise answer
                logger.warning("guardrail %s not answering for %s (%s); judged without it", name, request_id, answer)
                unavailable.append(name)
                continue
            reports[name] = apply_threshold(name, answer, thresholds.get(name))
        if skipped:
            logger.info("guardrails switched off: %s", ", ".join(skipped))
        return merge(reports, skipped=skipped, unavailable=unavailable, n_pages=n_pages)

    async def _ask(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        client = self._endpoints[name].client
        assert client is not None
        answer = await client.post_json(PATHS[name], body)
        data = answer.get("data") if isinstance(answer, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("passed"), bool):
            raise InternalError(f"{client.name} returned an unexpected response")
        return data

    async def aclose(self) -> None:
        for endpoint in self._endpoints.values():
            if endpoint.client is not None:
                await endpoint.client.aclose()
