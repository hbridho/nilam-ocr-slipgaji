"""Each guardrail's answer is kept in `nilam_guardrails_results`, one row per guardrail, by the OCR job."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import MetaData, select

from ocr_common.clients.guardrails import apply_threshold, merge
from ocr_common.pipeline import STAGE_OCR, InMemoryJobRepository, StagePipeline
from ocr_common.pipeline.database import dispose_engines, get_engine
from ocr_common.pipeline.tables import guardrails_results_table
from ocr_common.testing import RecordingCallback, RecordingNextStage, image_upload, make_client, wait_for_job

from app.dependencies import get_extraction_service, get_job_service
from app.main import app
from app.services.guardrails_log import SqlGuardrailsLog, guardrail_rows
from app.services.job_service import ExtractionJobService

NOW = datetime(2026, 10, 7, 9, 30, tzinfo=UTC)

BLANK_OK = {"check": "blank", "verdict": "ok", "passed": True, "reason": None, "chars": 1789, "max_chars": 20}
BLUR_OK = {"check": "blur", "verdict": "ok", "passed": True, "reason": None, "p_broken": 0.0153, "threshold": 0.9584}
IDENTITY = {
    "check": "identity",
    "verdict": "slip_gaji",
    "passed": True,
    "reason": None,
    "proba_slip_gaji": 0.1329,
    "reject_threshold": 0.47,
}


def _report(**reports) -> dict:
    full = dict.fromkeys(("blank", "blur", "identity"))
    full.update(reports)
    return merge(full, skipped=[], unavailable=[], n_pages=2)


def test_one_row_per_guardrail_each_with_its_own_answer():
    # The request's identity threshold (0.5) holds the document back; blur and blank let it through.
    identity = apply_threshold("identity", dict(IDENTITY), 0.5)
    report = _report(blank=BLANK_OK, blur=BLUR_OK, identity=identity)

    rows = {row["guardrail"]: row for row in guardrail_rows("REQ_1", report, sequence=None, now=NOW)}

    assert list(rows) == ["blank", "blur", "identity"]
    assert {row["request_id"] for row in rows.values()} == {"REQ_1"}
    assert {row["n_pages"] for row in rows.values()} == {2}
    assert {row["ds"] for row in rows.values()} == {"20261007"}

    assert rows["blank"] | {"report": None} == rows["blank"] | {
        "passed": True,
        "verdict": "ok",
        "confidence": None,
        "threshold": None,
        "threshold_source": "service",
        "reason": None,
        "report": None,
    }
    assert rows["blank"]["report"]["max_chars"] == 20
    # blur: its own threshold is on P(broken); the row says it on the accept side, like every other row.
    assert (rows["blur"]["passed"], rows["blur"]["confidence"], rows["blur"]["threshold"]) == (True, 0.9847, 0.0416)
    assert rows["blur"]["threshold_source"] == "service"
    assert rows["identity"] | {"report": None} == rows["identity"] | {
        "passed": False,
        "verdict": "bukan_slip_gaji",
        "confidence": 0.1329,
        "threshold": 0.5,
        "threshold_source": "request",
        "reason": "Dokumen ini bukan slip gaji. Mohon unggah slip gaji.",
        "report": None,
    }


def test_a_guardrail_switched_off_or_not_answering_still_gets_its_row():
    report = merge(
        {"blank": BLANK_OK, "blur": None, "identity": None}, skipped=["identity"], unavailable=["blur"], n_pages=1
    )

    rows = {row["guardrail"]: row for row in guardrail_rows("REQ_2", report, sequence=["guardrails"], now=NOW)}

    assert (rows["blur"]["verdict"], rows["blur"]["passed"]) == ("unavailable", True)
    assert (rows["identity"]["verdict"], rows["identity"]["passed"]) == ("skipped", True)
    assert [row["pipeline_name_sequence"] for row in rows.values()] == [["guardrails"]] * 3


@pytest.fixture
async def database(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'guardrails.db').as_posix()}"
    metadata = MetaData()
    guardrails_results_table(metadata)
    guardrails_results_table(metadata, "testing_")
    async with get_engine(url).begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield url
    await dispose_engines()


async def _rows(url: str, table_prefix: str = "") -> list:
    table = guardrails_results_table(MetaData(), table_prefix)
    async with get_engine(url).connect() as conn:
        return (await conn.execute(select(table).order_by(table.c.id))).all()


async def test_the_rows_land_in_the_table(database):
    report = _report(blank=BLANK_OK, blur=BLUR_OK, identity=IDENTITY)

    await SqlGuardrailsLog(database).record("REQ_3", report, sequence=["guardrails", "extraction"])
    await SqlGuardrailsLog(database, table_prefix="testing_").record("REQ_T", report, sequence=None)

    rows = await _rows(database)
    assert [(r.request_id, r.guardrail, r.passed, r.verdict) for r in rows] == [
        ("REQ_3", "blank", True, "ok"),
        ("REQ_3", "blur", True, "ok"),
        ("REQ_3", "identity", True, "slip_gaji"),
    ]
    assert rows[2].report["proba_slip_gaji"] == 0.1329
    assert rows[0].pipeline_name_sequence == ["guardrails", "extraction"]
    assert [r.request_id for r in await _rows(database, "testing_")] == ["REQ_T"] * 3


async def test_a_database_that_cannot_be_written_never_fails_the_caller(tmp_path):
    unreachable = f"sqlite+aiosqlite:///{(tmp_path / 'nope' / 'x.db').as_posix()}"

    await SqlGuardrailsLog(unreachable).record("REQ_4", _report(blank=BLANK_OK), sequence=None)
    await dispose_engines()


# --- the OCR job writes them ------------------------------------------------------------------------------------


class RecordingLog:
    def __init__(self):
        self.calls: list[tuple[str, dict, list | None]] = []

    async def record(self, request_id, report, *, sequence):
        self.calls.append((request_id, report, sequence))


@pytest.fixture
def harness(monkeypatch):
    async def judged(request_id, text, confidence=None, *, n_pages, thresholds=None):
        identity = apply_threshold("identity", dict(IDENTITY), 0.5)
        return merge(
            {"blank": BLANK_OK, "blur": BLUR_OK, "identity": identity}, skipped=[], unavailable=[], n_pages=n_pages
        )

    monkeypatch.setattr(get_extraction_service()._guardrails, "check", judged)
    log = RecordingLog()
    pipeline = StagePipeline(
        stage=STAGE_OCR,
        repository=InMemoryJobRepository(),
        callback=RecordingCallback(),
        next_stage_client=RecordingNextStage(),
    )
    service = ExtractionJobService(
        pipeline, get_extraction_service(), 5 * 1024 * 1024, guardrails_log=log
    )  # ty: ignore[invalid-argument-type]
    app.dependency_overrides[get_job_service] = lambda: service
    with make_client(app) as client:
        yield client, log
    app.dependency_overrides.pop(get_job_service, None)


def _submit(client, auth, request_id, **data):
    return client.post(
        "/v1/extraction/jobs",
        headers=auth,
        data={"request_id": request_id, "document_type": "slip_gaji", **data},
        files=image_upload("slip_gaji.pdf", b"%PDF-1.4 fake", "application/pdf"),
    )


def test_the_ocr_job_records_the_verdict_of_a_rejected_document(harness, auth):
    client, log = harness

    _submit(client, auth, "REQ_held")
    job = wait_for_job(client, "/v1/extraction/jobs/REQ_held")

    assert job["result"]["reject_reason"], "identity holds it back"
    [(request_id, report, sequence)] = log.calls
    assert (request_id, sequence) == ("REQ_held", None)
    assert (report["passed"], report["rejected_by"]) == (False, "identity")
    assert report["checks"] == job["result"]["guardrails"]["checks"]


def test_a_sequence_without_guardrails_records_nothing(harness, auth):
    client, log = harness

    _submit(client, auth, "REQ_skip", pipeline_name_sequence='["extraction"]')
    wait_for_job(client, "/v1/extraction/jobs/REQ_skip")

    assert log.calls == []
