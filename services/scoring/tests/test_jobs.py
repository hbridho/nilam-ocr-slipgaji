import pytest

from ocr_common.pipeline import STAGE_SCORING, InMemoryJobRepository, StagePipeline
from ocr_common.testing import RecordingCallback, make_client, wait_for_job

from app.dependencies import get_confidence_service, get_job_service
from app.main import app
from app.services.job_service import ScoringJobService

DOC = "slip_gaji"
GUARDRAILS = {
    "passed": True,
    "reason": None,
    "document": {"verdict": "accepted", "proba_slip_gaji": 0.9934, "model": "slip_text"},
}
PAGE = "SLIP GAJI\nPeriode  : Februari 2025\nGaji Pokok  Rp 4.500.000\nTOTAL PENDAPATAN  Rp 5.100.000"


def _slip(slip_no=1, page=1, periode="2025-02"):
    return {
        "slip_no": slip_no,
        "page": page,
        "fields": {
            "nama_perusahaan": "PT SUMBER REJEKI MAKMUR",
            "periode": periode,
            "nama_karyawan": "ANDI SAPUTRA",
            "gaji_pokok": 4500000,
            "total_pendapatan": 5100000,
            "gaji_bersih": 4875000,
            "divisi": None,
        },
        "source": {"gaji_pokok": {"how": "regex", "label": "gaji pokok", "line": "Gaji Pokok  Rp 4.500.000"}},
        "checks": {"earnings": ["ok"]},
        "counts": {"filled": 6, "by_regex": 5, "by_derived": 1, "by_llm": 0, "chars": len(PAGE)},
        "llm": {"used": False, "asked": False, "error": None, "compare": {}, "disagree": []},
        "ocr_text": PAGE,
        "ocr_confidence": {"mean": 0.98, "n_boxes": 40, "n_low": 2},
        "missing_mandatory_fields": [],
    }


STRUCTURING = {"document_type": DOC, "n_slips": 1, "slips": [_slip()], "llm_used": False}


@pytest.fixture
def harness():
    callback = RecordingCallback()
    pipeline = StagePipeline(stage=STAGE_SCORING, repository=InMemoryJobRepository(), callback=callback)
    service = ScoringJobService(pipeline, get_confidence_service())
    app.dependency_overrides[get_job_service] = lambda: service
    with make_client(app) as client:
        yield client, callback
    app.dependency_overrides.pop(get_job_service, None)


def _payload(request_id, document_type=DOC, structuring=None):
    return {
        "request_id": request_id,
        "document_type": document_type,
        "guardrails": GUARDRAILS,
        "ocr": {"pages": [{"page": 1, "text": PAGE, "confidence": {"mean": 0.98}}]},
        "structuring": structuring if structuring is not None else STRUCTURING,
    }


def test_submit_returns_202_then_scores_and_sends_the_final_result(harness, auth):
    client, callback = harness

    response = client.post("/v1/scoring/jobs", headers=auth, json=_payload("REQ_1"))
    assert response.status_code == 202
    assert response.json()["data"] == {
        "request_id": "REQ_1",
        "stage": "SCORING",
        "status": "PROCESSING",
        "duplicate": False,
    }

    job = wait_for_job(client, "/v1/scoring/jobs/REQ_1")
    assert job["status"] == "DONE"
    result = job["result"]
    assert [entry["slip_no"] for entry in result["slips"]] == [1]
    assert all(0 <= score <= 1 for score in result["slips"][0]["scores"].values())
    assert result["threshold"] == 0.5

    [call] = callback.calls
    assert (call["stage"], call["status"]) == ("SCORING", "DONE")
    final = call["result"]
    assert final["document_type"] == DOC
    assert final["total_slip"] == 1
    assert final["guardrails"] == GUARDRAILS, "laporan guardrail dikembalikan apa adanya"
    assert final["slips"][0]["fields"]["gaji_pokok"] == 4500000
    assert final["slips"][0]["scores"]["gaji_pokok"] > 0


def test_only_fields_with_a_value_are_scored(harness, auth):
    """Tidak ada yang perlu dinilai pada field kosong, dan memberinya 0 akan tercampur dengan nilai
    yang benar-benar diragukan."""
    client, _ = harness
    client.post("/v1/scoring/jobs", headers=auth, json=_payload("REQ_empty"))

    scores = wait_for_job(client, "/v1/scoring/jobs/REQ_empty")["result"]["slips"][0]["scores"]

    assert "divisi" not in scores
    assert "gaji_pokok" in scores


def test_every_slip_of_a_three_month_document_is_scored(harness, auth):
    client, callback = harness
    structuring = {
        "document_type": DOC,
        "n_slips": 3,
        "slips": [_slip(1, 1, "2025-02"), _slip(2, 2, "2025-03"), _slip(3, 3, "2025-04")],
        "llm_used": False,
    }

    client.post("/v1/scoring/jobs", headers=auth, json=_payload("REQ_months", structuring=structuring))
    job = wait_for_job(client, "/v1/scoring/jobs/REQ_months")

    assert [entry["slip_no"] for entry in job["result"]["slips"]] == [1, 2, 3]
    assert callback.calls[0]["result"]["total_slip"] == 3


def test_same_request_id_is_not_scored_twice(harness, auth):
    client, callback = harness
    client.post("/v1/scoring/jobs", headers=auth, json=_payload("REQ_2"))
    wait_for_job(client, "/v1/scoring/jobs/REQ_2")

    again = client.post("/v1/scoring/jobs", headers=auth, json=_payload("REQ_2"))

    assert again.json()["data"]["duplicate"] is True
    assert len(callback.calls) == 1


def test_an_unsupported_document_type_fails_the_job(harness, auth):
    client, callback = harness
    client.post("/v1/scoring/jobs", headers=auth, json=_payload("REQ_3", document_type="ktp"))

    job = wait_for_job(client, "/v1/scoring/jobs/REQ_3")

    assert job["status"] == "FAILED"
    assert "Unsupported document_type: ktp" in job["error_message"]
    assert [(c["stage"], c["status"]) for c in callback.calls] == [("SCORING", "FAILED")]


def test_a_document_without_slips_fails_the_job(harness, auth):
    client, _ = harness
    empty = {"document_type": DOC, "n_slips": 0, "slips": [], "llm_used": False}

    client.post("/v1/scoring/jobs", headers=auth, json=_payload("REQ_4", structuring=empty))

    job = wait_for_job(client, "/v1/scoring/jobs/REQ_4")
    assert (job["status"], job["error_message"]) == ("FAILED", "tidak ada slip untuk dinilai")


def test_missing_structuring_without_a_database_is_422(harness, auth):
    client, _ = harness
    response = client.post("/v1/scoring/jobs", headers=auth, json={"request_id": "REQ_5"})

    assert response.status_code == 422
    assert "structuring tidak ada" in response.json()["message"]


def test_get_unknown_job_is_404(harness, auth):
    client, _ = harness
    assert client.get("/v1/scoring/jobs/REQ_missing", headers=auth).status_code == 404


def test_requires_api_key(harness):
    client, _ = harness
    assert client.post("/v1/scoring/jobs", json=_payload("REQ_6")).status_code == 401


class FakeResults:
    def __init__(self, **stored):
        self.stored = stored

    async def get(self, stage_prefix, request_id):
        return self.stored.get(stage_prefix, {}).get(request_id)


async def test_a_stale_job_is_run_again_from_what_the_database_holds():
    callback = RecordingCallback()
    pipeline = StagePipeline(stage=STAGE_SCORING, repository=InMemoryJobRepository(), callback=callback)
    service = ScoringJobService(
        pipeline, get_confidence_service(), results=FakeResults(structuring={"REQ_stale": STRUCTURING})
    )
    stored_input = {"document_type": DOC, "guardrails": GUARDRAILS}
    await pipeline.repository.claim("REQ_stale", input=stored_input)  # proses yang mengklaimnya mati di sini

    await service.resume("REQ_stale", stored_input)
    await pipeline.runner.drain(5)

    assert (await pipeline.get("REQ_stale"))["status"] == "DONE"
    assert callback.calls[0]["result"]["total_slip"] == 1
