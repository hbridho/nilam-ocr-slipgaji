"""Accept side only, and a field's threshold: its own, else the default (parsing: test_column_thresholds.py)."""

from typing import Any, cast

from ocr_common.slip_gaji import contract_data
from ocr_common.thresholds import field_threshold, passes
from ocr_common.types import FinalResult


def test_a_field_uses_its_own_then_an_old_all_field_then_the_default():
    assert field_threshold("gaji_bersih", {"gaji_bersih": 0.9}, 0.5) == 0.9
    # A job stored before all_field was spread at the entry point still carries it.
    assert field_threshold("gaji_pokok", {"all_field": 0.6}, 0.5) == 0.6
    assert field_threshold("gaji_pokok", None, 0.5) == 0.5


def test_the_accept_side_is_the_only_side():
    """API spec [07]: accept probability 0.7 passes thresholds 0.6 and 0.7 and fails 0.8."""
    assert passes(0.7, 0.6)
    assert passes(0.7, 0.7)
    assert not passes(0.7, 0.8)


def test_contract_confidence_follows_the_per_field_threshold():
    result = {
        "slips": [
            {
                "page": 1,
                "fields": {"gaji_pokok": 4500000, "gaji_bersih": 4000000},
                "scores": {"gaji_pokok": 0.7, "gaji_bersih": 0.7},
            }
        ]
    }

    [entry] = contract_data(cast(FinalResult, result), 0.5, {"gaji_bersih": 0.9})["slip"]
    slip = cast(dict[str, Any], entry)  # the 20 field keys are written from SLIP_FIELDS, not declared

    assert slip["gaji_pokok"] == {"value": 4500000, "confidence": 1}
    assert slip["gaji_bersih"] == {"value": 4000000, "confidence": 0}
    assert slip["bonus"] == {"value": None, "confidence": 0}
