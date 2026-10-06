"""The central orchestrator's thresholds, in their object form (2 Oct 2026), adapted to the 20 slip gaji fields."""

from typing import Any, cast

import pytest

from ocr_common.slip_gaji import SLIP_FIELDS, contract_data
from ocr_common.thresholds import InvalidThreshold, parse_column_thresholds, parse_guardrails_threshold
from ocr_common.types import FinalResult

RESULT = {
    "slips": [
        {
            "page": 1,
            "fields": {"gaji_pokok": 4500000, "nama_karyawan": "ANDI SAPUTRA"},
            "scores": {"gaji_pokok": 0.7296, "nama_karyawan": 0.9471},
        }
    ]
}


def test_the_central_orchestrators_thresholds_are_read():
    assert parse_column_thresholds('{"gaji_pokok": 0.9, "nama_karyawan": 0.5}') == {
        "gaji_pokok": 0.9,
        "nama_karyawan": 0.5,
    }
    assert parse_column_thresholds('{"gaji_bersih": 1}') == {"gaji_bersih": 1.0}


def test_all_field_is_spread_over_every_field_and_a_fields_own_key_wins():
    assert parse_column_thresholds('{"all_field": 0.8}') == dict.fromkeys(SLIP_FIELDS, 0.8)
    both_orders = ('{"all_field": 0.8, "gaji_pokok": 0.5}', '{"gaji_pokok": 0.5, "all_field": 0.8}')
    for raw in both_orders:
        assert parse_column_thresholds(raw) == {**dict.fromkeys(SLIP_FIELDS, 0.8), "gaji_pokok": 0.5}


@pytest.mark.parametrize("raw", [None, "", "   ", "{}"])
def test_nothing_given_means_the_default_for_every_field(raw):
    assert parse_column_thresholds(raw) is None


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("{not json", "valid JSON"),
        ("[0.9, 0.5]", "JSON object"),
        ('{"gaji": 0.9}', "unknown field(s) gaji"),
        ('{"all_fields": 0.9}', "unknown field(s) all_fields"),
        ('{"all_field": 0.9, "nomor_npwp": 0.9}', "unknown field(s) nomor_npwp"),
        ('{"all_field": 1.5}', "all_field must be between 0 and 1"),
        ('{"gaji_pokok": 1.5}', "between 0 and 1"),
        ('{"gaji_pokok": -0.1}', "between 0 and 1"),
        ('{"gaji_pokok": "tinggi"}', "must be a number"),
        ('{"gaji_pokok": true}', "must be a number"),
    ],
)
def test_anything_else_is_refused_with_the_reason(raw, reason):
    with pytest.raises(InvalidThreshold, match=reason.replace("(", r"\(").replace(")", r"\)")):
        parse_column_thresholds(raw)


def test_the_guardrails_threshold_is_read_from_its_object_keyed_by_guardrails_name():
    assert parse_guardrails_threshold('{"acc_rej": 0.8}') == {"identity": 0.8}
    assert parse_guardrails_threshold('{"identity": 0.3, "blur": 0.6}') == {"identity": 0.3, "blur": 0.6}


@pytest.mark.parametrize("raw", [None, "", "   ", "{}"])
def test_no_guardrails_threshold_means_the_guardrails_services_own(raw):
    assert parse_guardrails_threshold(raw) is None


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("0.3", "JSON object"),
        ("{not json", "valid JSON"),
        ("[0.3]", "JSON object"),
        ('{"accept": 0.3}', "unknown guardrails accept"),
        ('{"blank": 0.3}', "unknown guardrails blank"),
        ('{"acc_rej": "tinggi"}', "must be a number"),
        ('{"acc_rej": true}', "must be a number"),
        ('{"acc_rej": 0}', "between 0 and 1 \\(exclusive\\)"),
        ('{"acc_rej": 1}', "between 0 and 1 \\(exclusive\\)"),
        ('{"blur": 1.5}', "between 0 and 1"),
    ],
)
def test_any_other_guardrails_threshold_is_refused_with_the_reason(raw, reason):
    with pytest.raises(InvalidThreshold, match=reason):
        parse_guardrails_threshold(raw)


def test_each_field_uses_its_own_threshold_else_the_default():
    result = cast(FinalResult, RESULT)
    [entry] = contract_data(result, 0.5, {"gaji_pokok": 0.9})["slip"]
    slip = cast(dict[str, Any], entry)  # the 20 field keys are written from SLIP_FIELDS, not declared
    assert (slip["gaji_pokok"]["confidence"], slip["nama_karyawan"]["confidence"]) == (0, 1)
    assert cast(dict[str, Any], contract_data(result, 0.5)["slip"][0])["gaji_pokok"]["confidence"] == 1
