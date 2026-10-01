import pytest

from ocr_common.slip_gaji import contract_data
from ocr_common.thresholds import (
    InvalidThreshold,
    field_threshold,
    parse_column_thresholds,
    parse_guardrails_threshold,
    passes,
)


def test_nothing_sent_is_none():
    assert parse_guardrails_threshold(None, None) is None
    assert parse_column_thresholds(None) is None
    assert parse_column_thresholds("  ") is None


def test_a_plain_number_is_the_identity_threshold_on_the_accept_side():
    assert parse_guardrails_threshold("0.8", None) == {"identity": {"value": 0.8, "target": "accept"}}
    assert parse_guardrails_threshold("0.8", "rejected") == {"identity": {"value": 0.8, "target": "reject"}}


def test_an_object_sets_each_guardrail_and_acc_rej_means_identity():
    assert parse_guardrails_threshold('{"acc_rej": 0.7, "blur": 0.9}', "accepted") == {
        "identity": {"value": 0.7, "target": "accept"},
        "blur": {"value": 0.9, "target": "accept"},
    }


@pytest.mark.parametrize(
    ("raw", "tendency"),
    [
        ("1.5", None),
        ("0", None),
        ("abc", None),
        ('{"blank": 0.5}', None),
        ("{}", None),
        ("0.5", "maybe"),
        (None, "accepted"),
    ],
)
def test_an_unreadable_guardrails_threshold_is_refused(raw, tendency):
    with pytest.raises(InvalidThreshold):
        parse_guardrails_threshold(raw, tendency)


@pytest.mark.parametrize("raw", ['{"nama_npwp": 0.5}', '{"gaji_pokok": 2}', "[0.5]", "not json", "{}"])
def test_an_unreadable_column_threshold_is_refused(raw):
    with pytest.raises(InvalidThreshold):
        parse_column_thresholds(raw)


def test_a_field_uses_its_own_then_all_field_then_the_default():
    columns = parse_column_thresholds('{"gaji_bersih": 0.9, "all_field": 0.6}')
    assert field_threshold("gaji_bersih", columns, 0.5) == 0.9
    assert field_threshold("gaji_pokok", columns, 0.5) == 0.6
    assert field_threshold("gaji_pokok", None, 0.5) == 0.5


def test_accept_and_reject_sides():
    """API spec [07]: accept probability 0.7 passes thresholds 0.6 and 0.7 and fails 0.8 on the accept side."""
    assert passes(0.7, {"value": 0.6, "target": "accept"})
    assert passes(0.7, {"value": 0.7, "target": "accept"})
    assert not passes(0.7, {"value": 0.8, "target": "accept"})
    # Reject side: P(reject) = 0.3 is rejected only from threshold 0.3 down.
    assert passes(0.7, {"value": 0.5, "target": "reject"})
    assert not passes(0.7, {"value": 0.3, "target": "reject"})


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

    [slip] = contract_data(result, 0.5, {"gaji_bersih": 0.9})["slip"]

    assert slip["gaji_pokok"] == {"value": 4500000, "confidence": 1}
    assert slip["gaji_bersih"] == {"value": 4000000, "confidence": 0}
    assert slip["bonus"] == {"value": None, "confidence": 0}
