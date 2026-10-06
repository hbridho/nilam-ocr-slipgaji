import time

import httpx

from ocr_common.clients.remote import RemoteModelClient

from app.clients.extraction import ExtractionJobClient
from app.config import get_settings
from app.main import app
from app.services.extract_service import ExtractOcrService
from app.services.pipeline_waiter import STATUS_REJECTED, PipelineWaiter, WaitOutcome
from tests.conftest import JPEG, OCR_RESULT, SCORING_RESULT, STRUCTURING_RESULT

RID = "REQ_wait"


def _submit(client, auth, filename="slip_gaji.jpg"):
    return client.post(
        "/v1/extract-ocr", headers=auth, data={"request_id": RID}, files={"file": (filename, JPEG, "image/jpeg")}
    )


def _job(status, result=None, error_message=None):
    return {"status": status, "result": result, "error_message": error_message}


class FakeStage:
    def __init__(self, stage, *answers):
        self.stage = stage
        self._answers = list(answers)
        self.calls = 0

    async def get(self, request_id):
        self.calls += 1
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_finished_within_the_wait_is_200_with_the_final_result(client, auth, stub_waiter):
    response = _submit(client, auth)

    assert response.status_code == 200
    body = response.json()
    assert (body["guardrails"], body["errors"], body["pipeline_last_stage"]) == (0, None, None)
    assert body["data"]["total_slip"] == 2
    assert body["data"]["slip"][0]["gaji_pokok"] == {"value": 4500000, "confidence": 1}
    assert body["data"]["slip"][0]["nama_karyawan"] == {"value": "ANDI SAPUTRA", "confidence": 0}  # 0,42 < 0,5
    [(request_id, timeout)] = stub_waiter.calls
    assert request_id == RID
    assert 10 < timeout <= 15


def test_still_running_when_the_wait_runs_out_is_202(client, auth, stub_waiter):
    stub_waiter.outcome = WaitOutcome("STRUCTURING", "PROCESSING", results={"OCR": OCR_RESULT})

    response = _submit(client, auth)

    assert response.status_code == 202
    body = response.json()
    assert (body["status_code"], body["status_desc"], body["message"]) == (
        202,
        "Accepted",
        "OCR job accepted; still processing",
    )
    assert (body["data"], body["guardrails"], body["errors"], body["pipeline_last_stage"]) == (None, None, None, None)


def test_failure_within_the_wait_is_422_with_the_failed_stage(client, auth, stub_waiter):
    stub_waiter.outcome = WaitOutcome("OCR", "FAILED", "extraction OCR model is unavailable")

    response = _submit(client, auth)

    assert response.status_code == 422
    body = response.json()
    assert (body["errors"], body["message"]) == ("OCR_FAILED", "extraction OCR model is unavailable")
    assert (body["data"], body["guardrails"], body["pipeline_last_stage"]) == (None, 0, "extraction")


def test_a_document_held_by_the_guardrail_is_400_with_its_reason(client, auth, stub_waiter):
    """Guardrail berjalan di tahap OCR (modelnya membaca teks OCR), jadi penolakannya sampai ke sini
    sebagai penolakan pipeline — bukan sebagai jawaban sebelum pipeline berjalan."""
    reason = "Dokumen ini bukan slip gaji. Mohon unggah slip gaji."
    stub_waiter.outcome = WaitOutcome("GUARDRAILS", STATUS_REJECTED, reason)

    response = _submit(client, auth)

    assert response.status_code == 400
    body = response.json()
    assert (body["errors"], body["message"]) == ("DOWNSTREAM_VALIDATION_ERROR", reason)
    assert (body["data"], body["guardrails"], body["pipeline_last_stage"]) == (None, 1, "guardrails")


def test_rejection_by_the_structuring_rules_is_also_400(client, auth, stub_waiter):
    reason = "Tidak ada satu pun field wajib yang terbaca pada dokumen ini"
    stub_waiter.outcome = WaitOutcome("STRUCTURING", STATUS_REJECTED, reason)

    response = _submit(client, auth)

    assert response.status_code == 400
    assert response.json()["guardrails"] == 1


def test_waiting_disabled_answers_202_right_after_the_handoff(client, auth, stub_waiter):
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update={"pipeline_wait_seconds": 0})
    try:
        response = _submit(client, auth)
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 202
    assert (response.json()["data"], response.json()["guardrails"]) == (None, None)
    assert stub_waiter.calls == []


async def test_the_wait_is_counted_from_the_arrival_of_the_request(stub_waiter):
    remote = RemoteModelClient(
        "http://extraction:8030",
        5.0,
        name="extraction service",
        passthrough_client_errors=True,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                202, json={"data": {"request_id": RID, "stage": "OCR", "status": "PROCESSING", "duplicate": False}}
            )
        ),
    )
    settings = get_settings().model_copy(update={"pipeline_wait_seconds": 15})
    service = ExtractOcrService(ExtractionJobClient(remote, attempts=1, delay=0), stub_waiter, settings)

    await service.submit(RID, "slip_gaji", "slip_gaji.jpg", "image/jpeg", JPEG, received_at=time.monotonic() - 10)

    [(_, timeout)] = stub_waiter.calls
    assert 4 < timeout <= 5


async def test_waiter_follows_the_stages_in_order_and_collects_their_results():
    stages = [
        FakeStage("OCR", _job("PROCESSING"), _job("DONE", OCR_RESULT)),
        FakeStage("STRUCTURING", None, _job("DONE", STRUCTURING_RESULT)),
        FakeStage("SCORING", _job("DONE", SCORING_RESULT)),
    ]

    outcome = await PipelineWaiter(stages, poll_interval=0.01).wait(RID, 5)

    assert (outcome.stage, outcome.status, outcome.error_message) == ("SCORING", "DONE", None)
    assert outcome.results == {"OCR": OCR_RESULT, "STRUCTURING": STRUCTURING_RESULT, "SCORING": SCORING_RESULT}
    assert [stage.calls for stage in stages] == [2, 2, 1]


async def test_waiter_stops_at_the_first_failed_stage():
    stages = [
        FakeStage("OCR", _job("DONE", {})),
        FakeStage("STRUCTURING", _job("FAILED", error_message="tidak ada teks OCR yang bisa distrukturkan")),
        FakeStage("SCORING", _job("DONE", {})),
    ]

    outcome = await PipelineWaiter(stages, poll_interval=0.01).wait(RID, 5)

    assert (outcome.stage, outcome.status, outcome.error_message) == (
        "STRUCTURING",
        "FAILED",
        "tidak ada teks OCR yang bisa distrukturkan",
    )
    assert stages[2].calls == 0


async def test_waiter_stops_at_a_guardrail_rejection_in_the_ocr_stage():
    """Job OCR-nya DONE — hasil OCR tersimpan — tetapi `reject_reason` mengakhiri pipeline di situ."""
    reason = "Dokumen ini bukan slip gaji. Mohon unggah slip gaji."
    stages = [
        FakeStage("OCR", _job("DONE", {**OCR_RESULT, "reject_reason": reason})),
        FakeStage("STRUCTURING", _job("DONE", STRUCTURING_RESULT)),
        FakeStage("SCORING", _job("DONE", SCORING_RESULT)),
    ]

    outcome = await PipelineWaiter(stages, poll_interval=0.01).wait(RID, 5)

    assert (outcome.stage, outcome.status, outcome.error_message) == ("GUARDRAILS", STATUS_REJECTED, reason)
    assert stages[1].calls == 0, "dokumen yang ditahan tidak pernah sampai ke structuring"


async def test_waiter_stops_at_the_last_stage_of_the_sequence():
    stages = [
        FakeStage("OCR", _job("DONE", OCR_RESULT)),
        FakeStage("STRUCTURING", _job("DONE", STRUCTURING_RESULT)),
        FakeStage("SCORING", _job("DONE", SCORING_RESULT)),
    ]

    outcome = await PipelineWaiter(stages, poll_interval=0.01).wait(RID, 5, sequence=["extraction", "structuring"])

    assert (outcome.stage, outcome.status) == ("STRUCTURING", "DONE")
    assert stages[2].calls == 0
