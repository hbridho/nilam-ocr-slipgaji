"""The thresholds the central orchestrator may send with one request (API spec [07], as agreed 1-2 Oct 2026),
parsed and checked at the entry point so that a bad one is a 422 `INVALID_THRESHOLD` before anything runs.

Every score here is a probability on a 0-1 scale, and every threshold is on the ACCEPT side: a check passes,
or a field is confident, when its probability is at least the threshold. There is no `guardrails_tendency`.

`guardrails_confidence_threshold`
    A JSON object keyed by guardrail: `{"acc_rej": 0.8}` (the NPWP spelling, = `identity`), `{"identity": 0.8,
    "blur": 0.7}`. Values strictly between 0 and 1. A bare number is refused: the object form is the contract.
    identity: P(slip gaji) >= threshold. blur: P(page readable) = 1 - p_broken >= threshold. `blank` has no
    probability (it is a text-length rule) and takes no threshold.

`column_confidence_threshold`
    A JSON object `{field: threshold}` over the 20 slip fields; `all_field` is spread over every field and a
    field's own key wins over it. Values from 0 to 1. Fields not named use FIELD_CONFIDENCE_THRESHOLD (0.5).
"""

import json
from collections.abc import Mapping
from typing import Any

from ocr_common.slip_gaji import SLIP_FIELDS

GUARDRAIL_KEYS = {"acc_rej": "identity", "identity": "identity", "blur": "blur"}
ALL_FIELDS = "all_field"


class InvalidThreshold(ValueError):
    """A threshold parameter that cannot be read; the message says which and why."""


def _object(name: str, raw: str | None, example: str) -> dict[str, Any] | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise InvalidThreshold(f"{name} must be valid JSON, e.g. {example}") from exc
    if not isinstance(value, Mapping):
        raise InvalidThreshold(f"{name} must be a JSON object, e.g. {example}")
    return dict(value) or None


def _number(name: str, value: Any, *, exclusive: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidThreshold(f"{name} must be a number")
    number = float(value)
    if exclusive and not 0 < number < 1:
        raise InvalidThreshold(f"{name} must be between 0 and 1 (exclusive), got {value}")
    if not exclusive and not 0 <= number <= 1:
        raise InvalidThreshold(f"{name} must be between 0 and 1, got {value}")
    return number


def parse_guardrails_threshold(raw: str | None) -> dict[str, float] | None:
    """`{"identity": float, "blur": float}` (only the guardrails named), or None when nothing was sent."""
    given = _object("guardrails_confidence_threshold", raw, '{"acc_rej": 0.8}')
    if given is None:
        return None
    unknown = sorted(key for key in given if key not in GUARDRAIL_KEYS)
    if unknown:
        raise InvalidThreshold(
            f"guardrails_confidence_threshold: unknown guardrails {', '.join(unknown)}; "
            "expected acc_rej, identity, blur"
        )
    return {
        GUARDRAIL_KEYS[key]: _number(f"guardrails_confidence_threshold.{key}", value, exclusive=True)
        for key, value in given.items()
    }


def parse_column_thresholds(raw: str | None) -> dict[str, float] | None:
    """`{field: threshold}` with `all_field` already spread over the 20 fields, or None when nothing was sent."""
    given = _object("column_confidence_threshold", raw, '{"all_field": 0.8} or {"gaji_bersih": 0.9}')
    if given is None:
        return None
    unknown = sorted(set(given) - set(SLIP_FIELDS) - {ALL_FIELDS})
    if unknown:
        raise InvalidThreshold(f"column_confidence_threshold: unknown field(s) {', '.join(unknown)}")
    thresholds: dict[str, float] = {}
    if ALL_FIELDS in given:
        thresholds.update(dict.fromkeys(SLIP_FIELDS, _number(ALL_FIELDS, given.pop(ALL_FIELDS), exclusive=False)))
    for field, value in given.items():
        thresholds[field] = _number(f"column_confidence_threshold.{field}", value, exclusive=False)
    return thresholds


def field_threshold(name: str, columns: Mapping[str, float] | None, default: float) -> float:
    """The threshold of one field: its own (an `all_field` from an older job also counts), else `default`."""
    columns = columns or {}
    if name in columns:
        return float(columns[name])
    if ALL_FIELDS in columns:
        return float(columns[ALL_FIELDS])
    return float(default)


def passes(accept_probability: float, threshold: float) -> bool:
    """Accept side, the only side: passes when the accept probability reaches the threshold."""
    return accept_probability >= float(threshold)
