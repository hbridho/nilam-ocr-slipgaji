import httpx
import pytest

from ocr_common.clients.remote import RemoteModelClient
from ocr_common.errors import ServiceError, UpstreamUnavailable

from app.clients.stages import StageStatusClient, build_stage_status_clients
from app.config import get_settings
from app.services.pipeline_waiter import STATUS_REJECTED, PipelineWaiter, WaitOutcome

RID = "REQ_status"


def _job(status, result=None, error_message=None):
    return {"status": status, "result": result, "error_message": error_message}


class FakeStage:
    def __init__(self, stage, answer):
        self.stage = stage
        self._answer = answer
        self.calls = 0

    async def get(self, request_id):
        self.calls += 1
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def _stages(ocr, structuring=None, scoring=None) -> list[FakeStage]:
    return [FakeStage("OCR", ocr), FakeStage("STRUCTURING", structuring), FakeStage("SCORING", scoring)]


def _get(client, auth, request_id=RID):
    return client.get(f"/v1/extract-ocr/{request_id}", headers=auth)


# --- the endpoint -----------------------------------------------------------------------------------


def test_finished_request_is_200_with_its_data_and_no_params(client, auth, stub_waiter):
    response = _get(client, auth)

    assert response.status_code == 200
    body = response.json()
    assert (body["status_code"], body["status_desc"]) == (200, "OK")
    assert body["message"] == "OCR extraction completed successfully"
    assert body["request_id"] == RID
    assert (body["guardrails"], body["errors"], body["params"], body["pipeline_last_stage"]) == (
        0,
        None,
        None,
        "scoring",
    )
    assert body["data"]["total_slip"] == 2
    assert stub_waiter.snapshots == [RID]
    assert stub_waiter.calls == [], "the status is read, not waited for"


def test_running_request_is_202(client, auth, stub_waiter):
    stub_waiter.snapshot_outcome = WaitOutcome("STRUCTURING", "PROCESSING")

    response = _get(client, auth)

    assert response.status_code == 202
    assert (response.json()["pipeline_last_stage"], response.json()["guardrails"]) == ("structuring", None)


def test_failed_stage_is_422(client, auth, stub_waiter):
    stub_waiter.snapshot_outcome = WaitOutcome("SCORING", "FAILED", "model keyakinan tidak tersedia")

    body = _get(client, auth).json()

    assert (body["status_code"], body["errors"], body["message"]) == (
        422,
        "SCORING_FAILED",
        "model keyakinan tidak tersedia",
    )


def test_a_guardrail_rejection_is_400(client, auth, stub_waiter):
    reason = "Dokumen ini bukan slip gaji. Mohon unggah slip gaji."
    stub_waiter.snapshot_outcome = WaitOutcome("GUARDRAILS", STATUS_REJECTED, reason)

    body = _get(client, auth).json()

    assert (body["status_code"], body["errors"], body["message"], body["guardrails"], body["pipeline_last_stage"]) == (
        400,
        "DOWNSTREAM_VALIDATION_ERROR",
        reason,
        1,
        "guardrails",
    )


def test_unknown_request_id_is_404(client, auth, stub_waiter):
    stub_waiter.snapshot_outcome = None

    response = _get(client, auth, "REQ_unknown")

    assert response.status_code == 404
    body = response.json()
    assert (body["message"], body["request_id"]) == ("No request found for request_id REQ_unknown", "REQ_unknown")


def test_unreadable_stage_is_503_not_a_false_202(client, auth, stub_waiter):
    stub_waiter.snapshot_error = UpstreamUnavailable("structuring service is unavailable")

    response = _get(client, auth)

    assert response.status_code == 503
    assert response.json()["message"] == "structuring service is unavailable"


def test_status_needs_the_api_key(client):
    assert client.get(f"/v1/extract-ocr/{RID}").status_code == 401


# --- PipelineWaiter.snapshot --------------------------------------------------------------------------


async def test_snapshot_of_a_finished_pipeline_collects_every_result():
    stages = _stages(
        _job("DONE", {"pages": []}),
        _job("DONE", {"slips": []}),
        _job("DONE", {"slips": [{"slip_no": 1, "scores": {"gaji_pokok": 0.936}}]}),
    )

    outcome = await PipelineWaiter(stages, poll_interval=0.01).snapshot(RID)

    assert outcome is not None
    assert (outcome.stage, outcome.status) == ("SCORING", "DONE")
    assert outcome.results["SCORING"] == {"slips": [{"slip_no": 1, "scores": {"gaji_pokok": 0.936}}]}
    assert [stage.calls for stage in stages] == [1, 1, 1]


async def test_snapshot_without_an_ocr_job_is_none():
    stages = _stages(None)

    assert await PipelineWaiter(stages, poll_interval=0.01).snapshot(RID) is None
    assert [stage.calls for stage in stages] == [1, 0, 0]


async def test_snapshot_stops_at_the_first_running_stage():
    stages = _stages(_job("DONE", {}), _job("PROCESSING"))

    outcome = await PipelineWaiter(stages, poll_interval=0.01).snapshot(RID)

    assert outcome is not None
    assert (outcome.stage, outcome.status) == ("STRUCTURING", "PROCESSING")
    assert stages[2].calls == 0


async def test_snapshot_reads_a_next_stage_without_a_job_yet_as_running():
    """The hand-off is still on its way (or, for good, lost: see the endpoint's limitation)."""
    stages = _stages(_job("DONE", {}), None)

    outcome = await PipelineWaiter(stages, poll_interval=0.01).snapshot(RID)

    assert outcome is not None
    assert (outcome.stage, outcome.status) == ("STRUCTURING", "PROCESSING")


async def test_snapshot_stops_at_a_failed_stage():
    stages = _stages(_job("FAILED", error_message="extraction OCR model is unavailable"))

    outcome = await PipelineWaiter(stages, poll_interval=0.01).snapshot(RID)

    assert outcome is not None
    assert (outcome.stage, outcome.status, outcome.error_message) == (
        "OCR",
        "FAILED",
        "extraction OCR model is unavailable",
    )
    assert [stage.calls for stage in stages] == [1, 0, 0]


async def test_snapshot_reports_a_rejection_even_though_scoring_has_no_job():
    reason = "dokumen blur / blank"
    stages = _stages(_job("DONE", {}), _job("DONE", {"fields": {}, "flag": True, "reject_reason": reason}), None)

    outcome = await PipelineWaiter(stages, poll_interval=0.01).snapshot(RID)

    assert outcome is not None
    assert (outcome.stage, outcome.status, outcome.error_message) == ("STRUCTURING", STATUS_REJECTED, reason)
    assert stages[2].calls == 0


async def test_snapshot_raises_when_a_stage_cannot_be_read():
    stages = _stages(_job("DONE", {}), ServiceError(503, "structuring service is unavailable"))

    with pytest.raises(ServiceError) as exc:
        await PipelineWaiter(stages, poll_interval=0.01).snapshot(RID)
    assert exc.value.status_code == 503


async def test_snapshot_refuses_a_status_it_does_not_know():
    with pytest.raises(ServiceError) as exc:
        await PipelineWaiter(_stages(_job("QUEUED")), poll_interval=0.01).snapshot(RID)
    assert exc.value.status_code == 500


# --- the stage clients ---------------------------------------------------------------------------------


async def test_a_stage_answering_404_is_no_job_and_401_is_500_not_passed_on():
    """Configured like build_stage_status_clients: a 401 from a stage is our misconfiguration; passed on, the
    central orchestrator would read it as its own key being wrong."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, json={"message": "No OCR job found"})
        return httpx.Response(401, json={"message": "Invalid API key"})

    remote = RemoteModelClient(
        "http://extraction:8030",
        5.0,
        name="extraction service",
        passthrough_statuses=(404,),
        transport=httpx.MockTransport(handler),
    )
    stage = StageStatusClient("OCR", remote, "/v1/extraction/jobs")

    assert await stage.get("missing") is None
    with pytest.raises(ServiceError) as exc:
        await stage.get(RID)
    assert (exc.value.status_code, exc.value.message) == (500, "extraction service error (401): Invalid API key")


def test_the_stage_clients_pass_only_404_through():
    for stage in build_stage_status_clients(get_settings()):
        assert stage._client._passthrough_statuses == frozenset({404})
        assert stage._client._passthrough is False


async def test_snapshot_reads_the_sequence_from_the_ocr_job_and_stops_at_its_last_stage():
    """A request that ended at structuring: GET must not wait for a scoring job that will never exist."""
    ocr = {**_job("DONE", {"pages": []}), "pipeline_name_sequence": ["extraction", "structuring"]}
    stages = _stages(ocr, _job("DONE", {"slips": []}), None)

    outcome = await PipelineWaiter(stages, poll_interval=0.01).snapshot(RID)

    assert outcome is not None
    assert (outcome.stage, outcome.status, outcome.sequence) == ("STRUCTURING", "DONE", ["extraction", "structuring"])
    assert stages[2].calls == 0


async def test_snapshot_of_guardrails_only_reads_the_ocr_job_alone():
    ocr = {**_job("DONE", {"guardrails": {"passed": True}}), "pipeline_name_sequence": ["guardrails"]}
    stages = _stages(ocr)

    outcome = await PipelineWaiter(stages, poll_interval=0.01).snapshot(RID)

    assert outcome is not None
    assert (outcome.stage, outcome.status) == ("OCR", "DONE")
    assert [stage.calls for stage in stages] == [1, 0, 0]


async def test_a_rejection_in_the_ocr_job_is_reported_as_the_guardrails():
    stages = _stages(_job("DONE", {"reject_reason": "Dokumen kosong"}))

    outcome = await PipelineWaiter(stages, poll_interval=0.01).snapshot(RID)

    assert outcome is not None
    assert (outcome.stage, outcome.status) == ("GUARDRAILS", STATUS_REJECTED)
