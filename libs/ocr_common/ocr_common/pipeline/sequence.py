"""`pipeline_name_sequence`: which services one request runs, chosen by the central orchestrator.

The public names and rules are the NILAM ones (API spec [07]):

    guardrails -> extraction -> structuring -> scoring

* `guardrails` may be left off the front; the tail may be cut; nothing may be skipped in the middle,
  repeated, reordered or misspelled.
* `["guardrails"]` alone is valid: the document is judged and nothing else is returned.

WHERE GUARDRAILS RUN, and why that differs from NPWP. The slip gaji guardrail models read OCR TEXT, not
pixels (measured: AUC 0.911 against 0.638 for pixels). So `guardrails` is a step of the OCR stage: the
extraction service reads the document, then calls the three guardrail services
(guardrail-blank, guardrail-blur, guardrail-identity) at the same time, before anything is handed on. A
sequence with `guardrails` therefore always pays for OCR — also `["guardrails"]` — and a document
rejected there is reported with `pipeline_last_stage: guardrails`.

The three guardrails stay three services, and each can be switched off on its own
(`GUARDRAIL_<NAME>_ENABLED`) without touching the sequence: that is a deployment choice, not a
per-request one.
"""

from collections.abc import Sequence
from typing import Any

from ocr_common.pipeline.stage import STAGE_OCR, STAGE_SCORING, STAGE_STRUCTURING, HandoffPayload

GUARDRAILS = "guardrails"
EXTRACTION = "extraction"
STRUCTURING = "structuring"
SCORING = "scoring"

PIPELINE_NAMES: tuple[str, ...] = (GUARDRAILS, EXTRACTION, STRUCTURING, SCORING)
DEFAULT_SEQUENCE = PIPELINE_NAMES

# The stage label of each pipeline service in jobs, callbacks and the orchestrator's tables. `guardrails` has
# no job of its own: it runs inside the OCR job, so its rejections are stored there.
STAGE_GUARDRAILS = "GUARDRAILS"
STAGE_OF = {
    GUARDRAILS: STAGE_GUARDRAILS,
    EXTRACTION: STAGE_OCR,
    STRUCTURING: STAGE_STRUCTURING,
    SCORING: STAGE_SCORING,
}
# And back: the pipeline service of a stage name, as pipeline_name_sequence and pipeline_last_stage name it.
SERVICE_OF_STAGE = {stage: name for name, stage in STAGE_OF.items()}

INVALID_ORDER_MESSAGE = (
    "Invalid pipeline_name_sequence: services must keep the order guardrails -> extraction -> structuring -> "
    "scoring without skipping one in the middle"
)


class InvalidSequence(ValueError):
    """The sequence breaks the rules above; the message says how."""


def validate_sequence(names: Sequence[str] | None) -> tuple[str, ...]:
    """The sequence to run: `names` when valid, the full pipeline when None or empty."""
    if not names:
        return DEFAULT_SEQUENCE
    names = tuple(names)
    unknown = [name for name in names if name not in PIPELINE_NAMES]
    if unknown:
        raise InvalidSequence(
            f"Invalid pipeline_name_sequence: unknown service {unknown[0]!r}; allowed: {', '.join(PIPELINE_NAMES)}"
        )
    if len(set(names)) != len(names):
        raise InvalidSequence("Invalid pipeline_name_sequence: a service is listed twice")
    start = PIPELINE_NAMES.index(names[0])
    # Only `guardrails` may be left off the front: every later service needs the result of the one before.
    if start > PIPELINE_NAMES.index(EXTRACTION):
        raise InvalidSequence(INVALID_ORDER_MESSAGE)
    if names != PIPELINE_NAMES[start : start + len(names)]:
        raise InvalidSequence(INVALID_ORDER_MESSAGE)
    return names


def runs_guardrails(sequence: Sequence[str] | None) -> bool:
    """True when the guardrails judge this request (the full pipeline, or a sequence that starts with them)."""
    return GUARDRAILS in validate_sequence(sequence)


def last_service(sequence: Sequence[str] | None) -> str:
    """The service whose result ends the request."""
    return validate_sequence(sequence)[-1]


def next_service(sequence: Sequence[str] | None, name: str) -> str | None:
    """The service after `name` in `sequence` (validated; None = the full pipeline), or None when `name`
    is the last one. Raises `InvalidSequence` when `name` is not in it."""
    sequence = validate_sequence(sequence)
    if name not in sequence:
        raise InvalidSequence(f"pipeline_name_sequence does not include {name}: {list(sequence)}")
    index = sequence.index(name)
    return sequence[index + 1] if index + 1 < len(sequence) else None


def checked_sequence(names: Sequence[str] | None, name: str) -> list[str] | None:
    """A stage's check of the sequence it was sent: valid and including `name`. None stays None (the full
    pipeline). Raises `InvalidSequence`, a ValueError, so a Pydantic validator turns it into a 422."""
    if not names:
        return None
    sequence = validate_sequence(names)
    if name not in sequence:
        raise InvalidSequence(f"pipeline_name_sequence does not include {name}: {list(sequence)}")
    return list(sequence)


def chain(sequence: Sequence[str] | None, name: str, handoff_payload: HandoffPayload | None) -> dict[str, Any]:
    """The `StagePipeline.submit` / `resume` arguments that continue the request after `name`: a
    hand-off to the next service of `sequence`, or, when `name` is the last one, nothing to hand on
    (`next_stage` None: the stage's DONE callback carries `final: true`)."""
    following = next_service(sequence, name)
    if following is None:
        return {"handoff_payload": None, "next_stage": None}
    return {"handoff_payload": handoff_payload, "next_stage": STAGE_OF[following]}


def stored(sequence: Sequence[str] | None) -> list[str] | None:
    """The sequence as a job's `input` and a hand-off body carry it (JSON); None for the full pipeline."""
    return list(sequence) if sequence else None
