import pytest

from ocr_common.pipeline import (
    DEFAULT_SEQUENCE,
    InMemoryJobRepository,
    InvalidSequence,
    chain,
    checked_sequence,
    last_service,
    next_service,
    runs_guardrails,
    validate_sequence,
)
from ocr_common.pipeline.callbacks import result_callback_body, stage_callback_body

FULL = ["guardrails", "extraction", "structuring", "scoring"]


@pytest.mark.parametrize(
    "sequence",
    [
        FULL,
        ["guardrails", "extraction", "structuring"],
        ["guardrails", "extraction"],
        ["guardrails"],
        ["extraction", "structuring", "scoring"],
        ["extraction", "structuring"],
        ["extraction"],
    ],
)
def test_guardrails_may_be_left_off_the_front_and_the_end_cut(sequence):
    """API spec [07]: guardrails boleh dilewati dari depan; service di belakang boleh dipotong."""
    assert validate_sequence(sequence) == tuple(sequence)


@pytest.mark.parametrize(
    "sequence",
    [
        ["extraction", "scoring"],
        ["structuring", "scoring"],
        ["scoring"],
        ["guardrails", "structuring"],
        ["extraction", "guardrails"],
        ["structuring"],
    ],
)
def test_a_skipped_middle_or_a_wrong_order_is_refused(sequence):
    with pytest.raises(InvalidSequence, match="without skipping one in the middle"):
        validate_sequence(sequence)


@pytest.mark.parametrize(
    ("sequence", "reason"),
    [
        (["extraction", "extraction"], "listed twice"),
        (["ekstraksi"], "unknown service 'ekstraksi'"),
        (["guardrails_kosong"], "unknown service 'guardrails_kosong'"),
    ],
)
def test_an_unknown_or_repeated_name_is_refused(sequence, reason):
    with pytest.raises(InvalidSequence, match=reason):
        validate_sequence(sequence)


def test_no_sequence_is_the_full_pipeline():
    assert validate_sequence(None) == validate_sequence([]) == DEFAULT_SEQUENCE == tuple(FULL)
    assert next_service(None, "structuring") == "scoring"
    assert last_service(None) == "scoring"
    assert runs_guardrails(None) is True


def test_guardrails_run_only_when_the_sequence_names_them():
    assert runs_guardrails(["guardrails"]) is True
    assert runs_guardrails(["extraction", "structuring"]) is False


def test_next_service_is_none_for_the_last_one():
    sequence = ["extraction", "structuring"]

    assert next_service(sequence, "extraction") == "structuring"
    assert next_service(sequence, "structuring") is None
    with pytest.raises(InvalidSequence, match="does not include scoring"):
        next_service(["extraction"], "scoring")


def test_a_stage_checks_that_it_is_part_of_the_sequence():
    assert checked_sequence(None, "scoring") is None
    assert checked_sequence(("extraction", "structuring"), "structuring") == ["extraction", "structuring"]
    with pytest.raises(InvalidSequence):
        checked_sequence(["extraction"], "structuring")


def test_chain_hands_on_or_ends_the_request():
    def handoff(result):
        return {"handed": result}

    assert chain(None, "extraction", handoff) == {"handoff_payload": handoff, "next_stage": "STRUCTURING"}
    assert chain(FULL, "structuring", handoff)["next_stage"] == "SCORING"
    assert chain(["extraction"], "extraction", handoff) == {"handoff_payload": None, "next_stage": None}


def test_the_result_callback_completes_on_the_final_stage_with_its_result_as_it_is():
    ocr = {"full_text": "SLIP GAJI", "pages": []}
    final = stage_callback_body("REQ", "OCR", "DONE", result=ocr, final=True)
    not_final = stage_callback_body("REQ", "OCR", "DONE", result=ocr)

    assert final["final"] is True and "final" not in not_final
    assert result_callback_body(final) == {"request_id": "REQ", "status": "completed", "result": ocr, "guardrails": 0}
    assert result_callback_body(not_final) is None


def test_a_scoring_callback_queued_before_the_final_flag_still_completes_with_0_1_data():
    body = stage_callback_body(
        "REQ",
        "SCORING",
        "DONE",
        result={
            "slips": [
                {
                    "page": 1,
                    "fields": {"gaji_pokok": 4500000, "bonus": None},
                    "scores": {"gaji_pokok": 0.9361},
                    "missing_mandatory_fields": [],
                }
            ],
            "guardrails": {"passed": True},
        },
    )

    completed = result_callback_body(body)

    assert completed is not None and completed["status"] == "completed"
    [slip] = completed["result"]["slip"]
    assert slip["gaji_pokok"] == {"value": 4500000, "confidence": 1}
    assert slip["bonus"] == {"value": None, "confidence": 0}
    assert completed["guardrails"] == 0


async def test_a_job_record_carries_the_sequence_it_was_submitted_with():
    repository = InMemoryJobRepository()
    await repository.claim("REQ_1", input={"pipeline_name_sequence": ["extraction"]})
    await repository.claim("REQ_2", input={"document_type": "slip_gaji"})

    first, second = await repository.get("REQ_1"), await repository.get("REQ_2")

    assert first is not None and first["pipeline_name_sequence"] == ["extraction"]
    assert second is not None and second["pipeline_name_sequence"] is None
