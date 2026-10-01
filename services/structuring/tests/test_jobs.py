import pytest

from ocr_common.pipeline import STAGE_STRUCTURING, InMemoryJobRepository, StagePipeline
from ocr_common.testing import RecordingCallback, RecordingNextStage, make_client, wait_for_job
from ocr_common.types import OcrPage, StructuringResult

from app.dependencies import get_job_service, get_structuring_service
from app.main import app
from app.services.job_service import StructuringJobService
from app.services.structuring_service import StructuringService

DOC = "slip_gaji"
GUARDRAILS = {
    "passed": True,
    "reason": None,
    "verdict": "slip_gaji",
    "skipped": [],
    "unavailable": [],
    "document": {"verdict": "accepted", "confidence": 0.9934, "n_pages": 2, "threshold": 0.47},
}
PAGE = """PT SUMBER REJEKI MAKMUR
SLIP GAJI
Periode  : Februari 2025
Nama  : ANDI SAPUTRA
Gaji Pokok  Rp 4.500.000
TOTAL PENDAPATAN  Rp 5.100.000
TOTAL POTONGAN  Rp 225.000
GAJI BERSIH  Rp 4.875.000
"""
OCR = {
    "engine": "mock",
    "model": None,
    "n_pages": 2,
    "full_text": PAGE + PAGE,
    "pages": [
        {"page": 1, "text": PAGE, "confidence": {"mean": 0.98}},
        {"page": 2, "text": PAGE.replace("Februari", "Maret"), "confidence": {"mean": 0.97}},
    ],
}


@pytest.fixture
def harness():
    callback, next_stage = RecordingCallback(), RecordingNextStage()
    pipeline = StagePipeline(
        stage=STAGE_STRUCTURING, repository=InMemoryJobRepository(), callback=callback, next_stage_client=next_stage
    )
    service = StructuringJobService(pipeline, get_structuring_service())
    app.dependency_overrides[get_job_service] = lambda: service
    with make_client(app) as client:
        yield client, callback, next_stage
    app.dependency_overrides.pop(get_job_service, None)


def _payload(request_id, ocr=OCR):
    return {"request_id": request_id, "document_type": DOC, "guardrails": GUARDRAILS, "ocr": ocr}


def test_submit_returns_202_then_structures_callback_and_handoff(harness, auth):
    client, callback, next_stage = harness

    response = client.post("/v1/structuring/jobs", headers=auth, json=_payload("REQ_1"))
    assert response.status_code == 202
    assert response.json()["request_id"] == "REQ_1"
    assert response.json()["data"] == {
        "request_id": "REQ_1",
        "stage": "STRUCTURING",
        "status": "PROCESSING",
        "duplicate": False,
    }

    job = wait_for_job(client, "/v1/structuring/jobs/REQ_1")
    assert job["status"] == "DONE"
    assert job["result"]["n_slips"] == 2
    assert job["result"]["slips"][0]["fields"]["nama_karyawan"] == "ANDI SAPUTRA"
    assert job["result"]["slips"][0]["fields"]["gaji_pokok"] == 4500000

    assert [(c["stage"], c["status"], c["result"]) for c in callback.calls] == [("STRUCTURING", "DONE", None)]
    [payload] = next_stage.payloads
    assert payload["request_id"] == "REQ_1"
    assert payload["guardrails"] == GUARDRAILS, "laporan guardrail diteruskan apa adanya sampai hasil akhir"
    assert payload["structuring"]["n_slips"] == 2


def test_every_page_of_a_multi_month_document_becomes_its_own_slip(harness, auth):
    client, _, _ = harness
    client.post("/v1/structuring/jobs", headers=auth, json=_payload("REQ_months"))

    slips = wait_for_job(client, "/v1/structuring/jobs/REQ_months")["result"]["slips"]

    assert [slip["page"] for slip in slips] == [1, 2]
    assert slips[0]["fields"]["periode"] != slips[1]["fields"]["periode"]


def test_same_request_id_is_not_processed_twice(harness, auth):
    client, callback, next_stage = harness
    client.post("/v1/structuring/jobs", headers=auth, json=_payload("REQ_2"))
    wait_for_job(client, "/v1/structuring/jobs/REQ_2")

    again = client.post("/v1/structuring/jobs", headers=auth, json=_payload("REQ_2"))
    assert again.status_code == 202
    assert again.json()["data"]["duplicate"] is True
    assert again.json()["data"]["status"] == "DONE"
    assert len(callback.calls) == 1
    assert len(next_stage.payloads) == 1


def test_no_text_fails_the_job_and_skips_handoff(harness, auth):
    client, callback, next_stage = harness
    response = client.post("/v1/structuring/jobs", headers=auth, json=_payload("REQ_3", {"pages": []}))
    assert response.status_code == 202

    job = wait_for_job(client, "/v1/structuring/jobs/REQ_3")
    assert (job["status"], job["error_message"]) == ("FAILED", "tidak ada teks OCR yang bisa distrukturkan")
    assert [(c["stage"], c["status"], c["error_message"]) for c in callback.calls] == [
        ("STRUCTURING", "FAILED", "tidak ada teks OCR yang bisa distrukturkan")
    ]
    assert next_stage.payloads == []


def test_missing_ocr_without_a_database_is_422(harness, auth):
    client, _, _ = harness
    response = client.post("/v1/structuring/jobs", headers=auth, json={"request_id": "REQ_4"})
    assert response.status_code == 422
    assert "ocr tidak ada" in response.json()["message"] or "ocr is missing" in response.json()["message"]


def test_get_unknown_job_is_404(harness, auth):
    client, _, _ = harness
    assert client.get("/v1/structuring/jobs/REQ_missing", headers=auth).status_code == 404


def test_requires_api_key(harness):
    client, _, _ = harness
    assert client.post("/v1/structuring/jobs", json=_payload("REQ_5")).status_code == 401


class FakeResults:
    """Berdiri menggantikan basis data bersama: {stage_prefix: {request_id: hasil}}."""

    def __init__(self, **stored):
        self.stored = stored

    async def get(self, stage_prefix, request_id):
        return self.stored.get(stage_prefix, {}).get(request_id)


@pytest.fixture
def reference_harness():
    callback, next_stage = RecordingCallback(), RecordingNextStage()
    pipeline = StagePipeline(
        stage=STAGE_STRUCTURING, repository=InMemoryJobRepository(), callback=callback, next_stage_client=next_stage
    )
    results = FakeResults(ocr={"REQ_ref": OCR})
    service = StructuringJobService(pipeline, get_structuring_service(), results=results, handoff_by_reference=True)
    app.dependency_overrides[get_job_service] = lambda: service
    with make_client(app) as client:
        yield client, next_stage
    app.dependency_overrides.pop(get_job_service, None)


def test_by_reference_reads_the_ocr_result_from_the_database_and_hands_off_a_reference(reference_harness, auth):
    client, next_stage = reference_harness
    body = {"request_id": "REQ_ref", "document_type": DOC, "guardrails": GUARDRAILS}

    assert client.post("/v1/structuring/jobs", headers=auth, json=body).status_code == 202
    job = wait_for_job(client, "/v1/structuring/jobs/REQ_ref")

    assert job["status"] == "DONE"
    assert job["result"]["slips"][0]["fields"]["gaji_pokok"] == 4500000
    assert next_stage.payloads == [
        {**body, "pipeline_name_sequence": None, "column_confidence_threshold": None}
    ], "tanpa ocr dan structuring di penyerahan: scoring membacanya sendiri"


def test_by_reference_fails_the_job_when_the_ocr_result_is_not_stored(reference_harness, auth):
    client, next_stage = reference_harness
    client.post("/v1/structuring/jobs", headers=auth, json={"request_id": "REQ_unknown", "document_type": DOC})
    job = wait_for_job(client, "/v1/structuring/jobs/REQ_unknown")

    assert job["status"] == "FAILED"
    assert "no ocr result stored for REQ_unknown" in job["error_message"]
    assert next_stage.payloads == []


async def test_a_stale_job_is_run_again_from_what_the_database_holds():
    callback, next_stage = RecordingCallback(), RecordingNextStage()
    pipeline = StagePipeline(
        stage=STAGE_STRUCTURING, repository=InMemoryJobRepository(), callback=callback, next_stage_client=next_stage
    )
    service = StructuringJobService(pipeline, get_structuring_service(), results=FakeResults(ocr={"REQ_stale": OCR}))
    stored_input = {"document_type": DOC, "guardrails": GUARDRAILS}
    await pipeline.repository.claim("REQ_stale", input=stored_input)  # proses yang mengklaimnya mati di sini

    await service.resume("REQ_stale", stored_input)
    await pipeline.runner.drain(5)

    assert (await pipeline.get("REQ_stale"))["status"] == "DONE"
    [payload] = next_stage.payloads
    assert (payload["guardrails"], payload["ocr"]) == (GUARDRAILS, OCR)
    assert [(c["stage"], c["status"]) for c in callback.calls] == [("STRUCTURING", "DONE")]


class _RejectingStructurer:
    """Berdiri menggantikan aturan structuring ketika sebuah pemeriksaan menolak dokumen.

    Untuk slip gaji penolakan biasanya terjadi di tahap OCR (di situlah guardrail berjalan), tetapi
    kait penolakan di tahap ini tetap ada dan harus tetap bekerja."""

    name = "rejecting"

    def structure(self, pages: list[OcrPage]) -> StructuringResult:
        return {
            "document_type": DOC,
            "slips": [],
            "n_slips": 0,
            "llm_used": False,
            "reject_reason": "Tidak ada satu pun field wajib yang terbaca pada dokumen ini",
        }


async def test_a_rejected_document_stops_at_structuring_with_a_failed_callback():
    callback, next_stage = RecordingCallback(), RecordingNextStage()
    pipeline = StagePipeline(
        stage=STAGE_STRUCTURING, repository=InMemoryJobRepository(), callback=callback, next_stage_client=next_stage
    )
    service = StructuringJobService(pipeline, StructuringService(_RejectingStructurer()))

    await service.submit("REQ_rejected", DOC, GUARDRAILS, OCR)
    await pipeline.runner.drain(5)

    job = await pipeline.get("REQ_rejected")
    assert job["status"] == "DONE", "hasilnya tetap bisa dibaca penantian dan penelusuran"
    assert job["result"]["reject_reason"] == "Tidak ada satu pun field wajib yang terbaca pada dokumen ini"
    assert next_stage.payloads == [], "dokumen yang ditolak tidak pernah sampai ke scoring"
    assert [(c["stage"], c["status"], c["error_message"], c["error_code"]) for c in callback.calls] == [
        (
            "STRUCTURING",
            "FAILED",
            "Tidak ada satu pun field wajib yang terbaca pada dokumen ini",
            "DOWNSTREAM_VALIDATION_ERROR",
        )
    ]


def test_a_sequence_ending_at_structuring_ends_the_request_here(harness, auth):
    client, callback, next_stage = harness
    body = {**_payload("REQ_end"), "pipeline_name_sequence": ["extraction", "structuring"]}

    assert client.post("/v1/structuring/jobs", headers=auth, json=body).status_code == 202
    job = wait_for_job(client, "/v1/structuring/jobs/REQ_end")

    assert job["status"] == "DONE"
    assert job["pipeline_name_sequence"] == ["extraction", "structuring"]
    assert next_stage.payloads == [], "structuring terakhir: tidak ada yang diserahkan ke scoring"
    [call] = callback.calls
    assert (call["stage"], call["status"], call["final"]) == ("STRUCTURING", "DONE", True)
    assert call["result"]["total_slip"] == 2
    assert call["result"]["slips"][0]["fields"]["gaji_pokok"] == 4500000


def test_sequence_and_column_thresholds_travel_to_scoring(harness, auth):
    client, _, next_stage = harness
    body = {**_payload("REQ_cols"), "column_confidence_threshold": {"gaji_bersih": 0.9}}

    client.post("/v1/structuring/jobs", headers=auth, json=body)
    wait_for_job(client, "/v1/structuring/jobs/REQ_cols")

    [payload] = next_stage.payloads
    assert payload["column_confidence_threshold"] == {"gaji_bersih": 0.9}
    assert payload["pipeline_name_sequence"] is None
