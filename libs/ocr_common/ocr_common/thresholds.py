"""The thresholds the central orchestrator may send with one request (API spec [07]), parsed and checked at
the entry point so that a bad one is a 422 `INVALID_THRESHOLD` before anything runs.

Every score here is a probability on a 0-1 scale.

`guardrails_confidence_threshold` + `guardrails_tendency`
    A number in (0, 1): the threshold of the identity guardrail (P(slip gaji)), the one guardrail that is a
    document-type classifier like the NPWP model. Or a JSON object per guardrail, e.g.
    `{"identity": 0.8, "blur": 0.7}`; `acc_rej` is accepted as another name of `identity` (the spelling of
    the NPWP examples). `blank` has no probability (it is a text-length rule) and takes no threshold.

    `accepted` (default): a check passes when its accept probability >= threshold.
    `rejected`: a check rejects when its reject probability >= threshold.
    For identity, accept = P(slip gaji); for blur, reject = P(page too broken to read).

`column_confidence_threshold`
    A JSON object `{field: threshold}` on the accept side: `confidence` is 1 when the confidence model's
    probability that the value is right >= threshold. `all_field` sets every field not named. Fields not
    named (and no `all_field`) use FIELD_CONFIDENCE_THRESHOLD (0.5).
"""

import json
from collections.abc import Mapping
from typing import Any, Literal

from ocr_common.slip_gaji import SLIP_FIELDS

Target = Literal["accept", "reject"]
TENDENCY_TARGET: dict[str, Target] = {"accepted": "accept", "rejected": "reject"}

GUARDRAIL_THRESHOLD_CHECKS = ("blur", "identity")
_CHECK_ALIASES = {"acc_rej": "identity", "identity": "identity", "blur": "blur"}
ALL_FIELDS = "all_field"


class InvalidThreshold(ValueError):
    """A threshold parameter that cannot be read; the message says which and why."""


def _probability(name: str, value: Any, *, open_interval: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise InvalidThreshold(f"{name} must be a number between 0 and 1")
    try:
        number = float(value)
    except ValueError as exc:
        raise InvalidThreshold(f"{name} must be a number between 0 and 1") from exc
    ok = 0 < number < 1 if open_interval else 0 <= number <= 1
    if not ok:
        bounds = "(0, 1)" if open_interval else "[0, 1]"
        raise InvalidThreshold(f"{name} must be in {bounds}, got {value}")
    return number


def parse_guardrails_threshold(raw: str | None, tendency: str | None) -> dict[str, dict[str, Any]] | None:
    """`{check: {"value": float, "target": "accept" | "reject"}}`, or None when nothing was sent."""
    tendency = (tendency or "").strip() or None
    if tendency is not None and tendency not in TENDENCY_TARGET:
        raise InvalidThreshold("guardrails_tendency must be 'accepted' or 'rejected'")
    if raw is None or not raw.strip():
        if tendency is not None:
            raise InvalidThreshold("guardrails_tendency needs guardrails_confidence_threshold")
        return None
    target = TENDENCY_TARGET[tendency or "accepted"]
    text = raw.strip()
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise InvalidThreshold("guardrails_confidence_threshold must be a number or a JSON object") from exc
    if isinstance(value, Mapping):
        if not value:
            raise InvalidThreshold("guardrails_confidence_threshold is an empty object")
        out: dict[str, dict[str, Any]] = {}
        for key, number in value.items():
            check = _CHECK_ALIASES.get(str(key))
            if check is None:
                raise InvalidThreshold(
                    f"guardrails_confidence_threshold: unknown guardrail {key!r}; allowed: identity, blur, acc_rej"
                )
            out[check] = {
                "value": _probability(f"guardrails_confidence_threshold.{key}", number, open_interval=True),
                "target": target,
            }
        return out
    return {
        "identity": {
            "value": _probability("guardrails_confidence_threshold", value, open_interval=True),
            "target": target,
        }
    }


def parse_column_thresholds(raw: str | None) -> dict[str, float] | None:
    """`{field: threshold}` (with `all_field` kept as given), or None when nothing was sent."""
    if raw is None or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise InvalidThreshold("column_confidence_threshold must be a JSON object") from exc
    if not isinstance(value, Mapping) or not value:
        raise InvalidThreshold("column_confidence_threshold must be a non-empty JSON object")
    out: dict[str, float] = {}
    for key, number in value.items():
        if key != ALL_FIELDS and key not in SLIP_FIELDS:
            raise InvalidThreshold(f"column_confidence_threshold: unknown field {key!r}")
        out[str(key)] = _probability(f"column_confidence_threshold.{key}", number, open_interval=False)
    return out


def field_threshold(name: str, columns: Mapping[str, float] | None, default: float) -> float:
    """The threshold of one field: its own, else `all_field`, else `default`."""
    columns = columns or {}
    if name in columns:
        return float(columns[name])
    if ALL_FIELDS in columns:
        return float(columns[ALL_FIELDS])
    return float(default)


def passes(accept_probability: float, threshold: Mapping[str, Any]) -> bool:
    """Whether a check with this accept probability passes under `{value, target}`."""
    value = float(threshold["value"])
    if threshold.get("target") == "reject":
        return (1.0 - accept_probability) < value
    return accept_probability >= value
