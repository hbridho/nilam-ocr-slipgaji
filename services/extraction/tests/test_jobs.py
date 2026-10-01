import pytest

from ocr_common.errors import ServiceError
from ocr_common.pipeline import STAGE_OCR, InMemoryJobRepository, StagePipeline
from ocr_common.testing import RecordingCallback, RecordingNextStage, image_upload, make_client, wait_for_job

from app.dependencies import get_extraction_service, get_job_service
from app.main import app
from app.services.job_service import ExtractionJobService

DOC = "slip_gaji"


@pytest.fixture
def harness():
    callback, next_stage = RecordingCallback(), RecordingNextStage()
    pipeline = StagePipeline(
        stage=STAGE_OCR, repository=InMemoryJobRepository(), callback=callback, next_stage_client=next_stage
    )
    service = ExtractionJobService(pipeline, get_extraction_service(), 5 * 1024 * 1024, simulate_delay=True)
    app.dependency_overrides[get_job_service] = lambda: service
    with make_client(app) as client:
        yield client, callback, next_stage
    app.dependency_overrides.pop(get_job_service, None)


def _submit(client, auth, request_id, filename="slip_gaji.pdf", data_extra=None, **kwargs):
    data = {"request_id": request_id, "document_type": DOC, **(data_extra or {})}
    return client.post(
        "/v1/extraction/jobs",
        headers=auth,
        data=data,
        files=image_upload(filename, b"%PDF-1.4 fake", "application/pdf"),
        **kwargs,
    )


def test_submit_returns_202_then_runs_ocr_callback_and_handoff(harness, auth):
    client, callback, next_stage = harness

    response = _submit(client, auth, "REQ_1")
    assert response.status_code == 202
    body = response.json()
    assert body["status_code"] == 202
    assert body["request_id"] == "REQ_1"
    assert body["data"] == {"request_id": "REQ_1", "stage": "OCR", "status": "PROCESSING", "duplicate": False}

    job = wait_for_job(client, "/v1/extraction/jobs/REQ_1")
    assert job["status"] == "DONE"
    assert job["result"]["pages"]

    assert [(c["stage"], c["status"], c["result"]) for c in callback.calls] == [("OCR", "DONE", None)]
    [payload] = next_stage.payloads
    assert payload["request_id"] == "REQ_1"
    assert payload["document_type"] == DOC
    # Laporan guardrail dihasilkan DI tahap ini dan diteruskan terpisah dari hasil OCR, supaya
    # structuring dan scoring menerimanya di tempat yang sama seperti tahap lain.
    assert payload["guardrails"]["passed"] is job["result"]["guardrails"]["passed"]
    assert payload["guardrails"]["skipped"] == ["blank", "blur", "identity"]  # dimatikan di conftest
    assert "guardrails" not in payload["ocr"]
    assert payload["ocr"]["pages"] == job["result"]["pages"]


def test_same_request_id_is_not_processed_twice(harness, auth):
    client, callback, next_stage = harness
    _submit(client, auth, "REQ_2")
    wait_for_job(client, "/v1/extraction/jobs/REQ_2")

    again = _submit(client, auth, "REQ_2")
    assert again.status_code == 202
    assert again.json()["data"] == {"request_id": "REQ_2", "stage": "OCR", "status": "DONE", "duplicate": True}
    assert len(callback.calls) == 1
    assert len(next_stage.payloads) == 1


def test_bad_file_fails_the_job_not_the_request(harness, auth):
    client, callback, next_stage = harness
    response = client.post(
        "/v1/extraction/jobs",
        headers=auth,
        data={"request_id": "REQ_3"},
        files={"file": ("slip.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 202

    job = wait_for_job(client, "/v1/extraction/jobs/REQ_3")
    assert job["status"] == "FAILED"
    assert job["error_message"]
    assert [(c["stage"], c["status"]) for c in callback.calls] == [("OCR", "FAILED")]
    assert callback.calls[0]["error_message"] == job["error_message"]
    assert next_stage.payloads == []


def test_a_document_held_by_the_guardrail_stops_here(harness, auth, monkeypatch):
    """Job tetap DONE — hasil OCR-nya tersimpan dan bisa ditelusuri — tetapi tidak diteruskan ke
    structuring, dan callbacknya FAILED dengan kode penolakan."""
    client, callback, next_stage = harness

    async def held(request_id, text, confidence=None, *, n_pages, thresholds=None):
        return {
            "passed": False,
            "reason": "Dokumen ini bukan slip gaji. Mohon unggah slip gaji.",
            "verdict": "bukan_slip_gaji",
            "rejected_by": "identity",
            "document": {
                "verdict": "reject",
                "confidence": 0.8671,
                "n_pages": n_pages,
                "threshold": 0.47,
                "threshold_target": "accept",
            },
            "checks": {"identity": {"proba_slip_gaji": 0.1329}},
            "skipped": [],
            "unavailable": [],
            "pages": [],
        }

    monkeypatch.setattr(get_extraction_service()._guardrails, "check", held)

    _submit(client, auth, "REQ_held")
    job = wait_for_job(client, "/v1/extraction/jobs/REQ_held")

    assert job["status"] == "DONE"
    assert job["result"]["reject_reason"] == "Dokumen ini bukan slip gaji. Mohon unggah slip gaji."
    assert job["result"]["guardrails"]["verdict"] == "bukan_slip_gaji"
    assert job["result"]["pages"], "hasil OCR harus tetap tersimpan"
    assert next_stage.payloads == []
    assert [(c["stage"], c["status"], c["error_code"]) for c in callback.calls] == [
        ("OCR", "FAILED", "DOWNSTREAM_VALIDATION_ERROR")
    ]


def test_handoff_failure_is_reported_as_structuring_failed(harness, auth):
    client, callback, next_stage = harness
    next_stage.error = ServiceError(503, "structuring service is unavailable")
    _submit(client, auth, "REQ_4")

    assert wait_for_job(client, "/v1/extraction/jobs/REQ_4")["status"] == "DONE"
    for _ in range(100):
        if len(callback.calls) == 2:
            break
        wait_for_job(client, "/v1/extraction/jobs/REQ_4")
    assert [(c["stage"], c["status"]) for c in callback.calls] == [("OCR", "DONE"), ("STRUCTURING", "FAILED")]


def test_file_url_is_downloaded_in_background(harness, auth, monkeypatch):
    client, callback, next_stage = harness

    async def fake_fetch(url, *, limit, timeout=10.0, policy):
        assert url == "http://minio:9000/bucket/slip_gaji.pdf?sig=x"
        return b"%PDF-1.4 fake", "slip_gaji.pdf", "application/pdf"

    monkeypatch.setattr("app.services.job_service.fetch", fake_fetch)
    response = client.post(
        "/v1/extraction/jobs",
        headers=auth,
        data={"request_id": "REQ_5", "file_url": "http://minio:9000/bucket/slip_gaji.pdf?sig=x"},
    )
    assert response.status_code == 202
    assert wait_for_job(client, "/v1/extraction/jobs/REQ_5")["status"] == "DONE"
    assert next_stage.payloads[0]["guardrails"]["verdict"] == "skipped"  # dimatikan di conftest


def test_requires_exactly_one_of_file_or_file_url(harness, auth):
    client, _, _ = harness
    response = client.post("/v1/extraction/jobs", headers=auth, data={"request_id": "REQ_6"})
    assert response.status_code == 400
    assert "Tidak ada berkas yang diterima" in response.json()["message"]


def test_a_sequence_without_guardrails_skips_them_and_carries_on(harness, auth):
    client, _, next_stage = harness
    _submit(client, auth, "REQ_noguard", data_extra={"pipeline_name_sequence": '["extraction","structuring"]'})

    job = wait_for_job(client, "/v1/extraction/jobs/REQ_noguard")

    assert job["pipeline_name_sequence"] == ["extraction", "structuring"]
    assert job["result"]["guardrails"] is None
    [payload] = next_stage.payloads
    assert payload["pipeline_name_sequence"] == ["extraction", "structuring"]


def test_a_sequence_ending_at_extraction_ends_the_request_with_the_ocr_result(harness, auth):
    client, callback, next_stage = harness
    _submit(client, auth, "REQ_ocr", data_extra={"pipeline_name_sequence": '["extraction"]'})

    wait_for_job(client, "/v1/extraction/jobs/REQ_ocr")

    assert next_stage.payloads == []
    [call] = callback.calls
    assert (call["stage"], call["status"], call["final"]) == ("OCR", "DONE", True)
    assert set(call["result"]) == {"engine", "model", "elapsed_ms", "n_pages", "full_text", "pages"}


def test_guardrails_only_ends_the_request_with_the_guardrail_report(harness, auth):
    client, callback, next_stage = harness
    _submit(client, auth, "REQ_guard", data_extra={"pipeline_name_sequence": '["guardrails"]'})

    wait_for_job(client, "/v1/extraction/jobs/REQ_guard")

    assert next_stage.payloads == []
    [call] = callback.calls
    assert call["final"] is True
    assert call["result"]["passed"] is True
    assert call["result"]["skipped"] == ["blank", "blur", "identity"]


def test_an_invalid_sequence_is_422(harness, auth):
    client, _, _ = harness
    response = _submit(client, auth, "REQ_bad", data_extra={"pipeline_name_sequence": '["structuring"]'})
    assert response.status_code == 422


def test_get_unknown_job_is_404(harness, auth):
    client, _, _ = harness
    response = client.get("/v1/extraction/jobs/REQ_missing", headers=auth)
    assert response.status_code == 404
    assert response.json()["request_id"] == "REQ_missing"


def test_requires_api_key(harness):
    client, _, _ = harness
    assert client.post("/v1/extraction/jobs", data={"request_id": "REQ_8"}).status_code == 401


def test_a_delay_token_in_the_file_name_holds_the_job_back_in_local_mode(harness, auth):
    client, _, _ = harness
    client.post(
        "/v1/extraction/jobs",
        headers=auth,
        data={"request_id": "REQ_slow", "document_type": DOC},
        files=image_upload("delay1s-slip_gaji.pdf", b"%PDF-1.4 fake", "application/pdf"),
    )

    assert wait_for_job(client, "/v1/extraction/jobs/REQ_slow", timeout=0.3)["status"] == "PROCESSING"
    assert wait_for_job(client, "/v1/extraction/jobs/REQ_slow", timeout=3)["status"] == "DONE"


def test_handoff_by_reference_leaves_the_ocr_result_out_of_the_payload(auth):
    callback, next_stage = RecordingCallback(), RecordingNextStage()
    pipeline = StagePipeline(
        stage=STAGE_OCR, repository=InMemoryJobRepository(), callback=callback, next_stage_client=next_stage
    )
    service = ExtractionJobService(
        pipeline, get_extraction_service(), 5 * 1024 * 1024, simulate_delay=True, handoff_by_reference=True
    )
    app.dependency_overrides[get_job_service] = lambda: service
    try:
        with make_client(app) as client:
            _submit(client, auth, "REQ_ref")
            job = wait_for_job(client, "/v1/extraction/jobs/REQ_ref")
    finally:
        app.dependency_overrides.pop(get_job_service, None)

    assert job["status"] == "DONE"
    [payload] = next_stage.payloads
    assert set(payload) == {
        "request_id",
        "document_type",
        "guardrails",
        "pipeline_name_sequence",
        "column_confidence_threshold",
    }
    assert payload["request_id"] == "REQ_ref"


def _stale_service():
    callback, next_stage = RecordingCallback(), RecordingNextStage()
    pipeline = StagePipeline(
        stage=STAGE_OCR, repository=InMemoryJobRepository(), callback=callback, next_stage_client=next_stage
    )
    return ExtractionJobService(pipeline, get_extraction_service(), 5 * 1024 * 1024), pipeline, callback, next_stage


async def test_a_stale_job_sent_as_file_url_is_fetched_and_run_again(monkeypatch):
    service, pipeline, callback, next_stage = _stale_service()
    stored_input = {
        "document_type": DOC,
        "file_url": "http://minio:9000/b/slip_gaji.pdf",
    }
    await pipeline.repository.claim("REQ_stale", input=stored_input)

    async def fake_fetch(url, *, limit, timeout=10.0, policy):
        assert url == stored_input["file_url"]
        return b"%PDF-1.4 fake", "slip_gaji.pdf", "application/pdf"

    monkeypatch.setattr("app.services.job_service.fetch", fake_fetch)
    await service.resume("REQ_stale", stored_input)
    await pipeline.runner.drain(5)

    assert (await pipeline.get("REQ_stale"))["status"] == "DONE"
    assert next_stage.payloads[0]["document_type"] == DOC
    assert [(c["stage"], c["status"]) for c in callback.calls] == [("OCR", "DONE")]


async def test_a_stale_job_of_an_inline_upload_fails_with_a_reason():
    """Unggahan inline ikut hilang bersama proses yang mati; hanya file_url yang bisa diambil ulang."""
    service, pipeline, callback, next_stage = _stale_service()
    stored_input = {"document_type": DOC, "file_url": None}
    await pipeline.repository.claim("REQ_gone", input=stored_input)

    await service.resume("REQ_gone", stored_input)
    await pipeline.runner.drain(5)

    job = await pipeline.get("REQ_gone")
    assert job["status"] == "FAILED"
    assert "diunggah inline" in job["error_message"]
    assert [(c["stage"], c["status"]) for c in callback.calls] == [("OCR", "FAILED")]
    assert next_stage.payloads == []
