"""`POST /v1/extract-ocr` against the NILAM API spec [07]: envelope, `pipeline_name_sequence`, thresholds,
and the file checks that always run first."""

import pytest

from ocr_common.testing import image_upload

from app.config import get_settings
from app.main import app
from app.services.pipeline_waiter import STATUS_REJECTED, WaitOutcome
from tests.conftest import ACCEPTED_REPORT, JPEG, OCR_RESULT, REJECTED_REASON, STRUCTURING_RESULT

TOO_MANY_PAGES = "Jumlah halaman melebihi batas, pastikan hanya mengunggah dokumen slip gaji"
ENVELOPE = {
    "status_code",
    "status_desc",
    "message",
    "data",
    "errors",
    "request_id",
    "guardrails",
    "pipeline_last_stage",
    "params",
}


def _submit(client, auth, filename="slip_gaji.jpg", content=JPEG, content_type="image/jpeg", **data):
    return client.post(
        "/v1/extract-ocr",
        data={"request_id": "OCR_1", **data},
        files=image_upload(filename, content, content_type),
        headers=auth,
    )


def _pdf(n_pages: int) -> bytes:
    import fitz

    document = fitz.open()
    for i in range(n_pages):
        document.new_page(width=300, height=200).insert_text((20, 40), f"halaman {i + 1}")
    return document.tobytes()


def _submit_pdf(client, auth, content, **data):
    return _submit(client, auth, filename="scan.pdf", content=content, content_type="application/pdf", **data)


@pytest.fixture
def settings_override():
    def install(**update):
        app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update=update)

    yield install
    app.dependency_overrides.pop(get_settings, None)


def test_health_has_the_spec_shape(client):
    body = client.get("/health").json()

    assert set(body) == {"status", "version", "detail", "device", "backends"}
    assert (body["status"], body["detail"]) == ("healthy", None)


def test_extract_ocr_follows_the_spec_envelope(client, auth, stub_extraction):
    response = _submit(client, auth)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == ENVELOPE
    assert (body["status_code"], body["status_desc"]) == (200, "OK")
    assert body["message"] == "OCR extraction completed successfully"
    assert (body["guardrails"], body["errors"], body["params"], body["pipeline_last_stage"]) == (
        0,
        None,
        None,
        "scoring",
    )
    assert body["data"]["total_slip"] == 2
    assert body["data"]["slip"][0]["periode"] == {"value": "2025-02", "confidence": 1}

    [handed] = stub_extraction.submitted
    assert (handed["request_id"], handed["document_type"], handed["sequence"]) == ("OCR_1", "slip_gaji", None)


def test_confidence_is_1_from_the_default_threshold_0_5(client, auth):
    """Skala 0-1: nama_karyawan 0,42 di bawah 0,5; gaji_pokok 0,936 di atasnya; field kosong selalu 0."""
    slip = _submit(client, auth).json()["data"]["slip"][0]

    assert slip["nama_karyawan"] == {"value": "ANDI SAPUTRA", "confidence": 0}
    assert slip["gaji_pokok"] == {"value": 4500000, "confidence": 1}
    assert slip["divisi"] == {"value": None, "confidence": 0}
    assert slip["bonus"] == {"value": None, "confidence": 0}


def test_column_confidence_threshold_moves_each_field(client, auth, stub_extraction):
    response = _submit(client, auth, column_confidence_threshold='{"gaji_pokok": 0.95, "nama_karyawan": 0.4}')

    slip = response.json()["data"]["slip"][0]
    assert slip["gaji_pokok"]["confidence"] == 0
    assert slip["nama_karyawan"]["confidence"] == 1
    assert stub_extraction.submitted[0]["columns"] == {"gaji_pokok": 0.95, "nama_karyawan": 0.4}


def test_all_field_sets_every_field_not_named(client, auth):
    slip = _submit(client, auth, column_confidence_threshold='{"all_field": 0.99}').json()["data"]["slip"][0]
    assert all(slip[name]["confidence"] == 0 for name in ("gaji_pokok", "periode", "gaji_bersih"))


def test_guardrails_threshold_is_normalised_and_handed_to_the_ocr_stage(client, auth, stub_extraction):
    _submit(client, auth, guardrails_confidence_threshold="0.8", guardrails_tendency="rejected")
    assert stub_extraction.submitted[0]["guardrail_thresholds"] == {"identity": {"value": 0.8, "target": "reject"}}


@pytest.mark.parametrize(
    "form",
    [
        {"guardrails_confidence_threshold": "1.5"},
        {"guardrails_confidence_threshold": '{"blank": 0.5}'},
        {"guardrails_tendency": "accepted"},
        {"column_confidence_threshold": '{"nomor_npwp": 0.9}'},
        {"column_confidence_threshold": "not json"},
    ],
)
def test_an_unreadable_threshold_is_422_before_anything_runs(client, auth, stub_extraction, form):
    response = _submit(client, auth, **form)

    assert response.status_code == 422
    body = response.json()
    assert (body["errors"], body["pipeline_last_stage"], body["guardrails"]) == (
        "INVALID_THRESHOLD",
        "orchestrator",
        None,
    )
    assert stub_extraction.submitted == []


@pytest.mark.parametrize(
    "sequence",
    [
        '["extraction","scoring"]',
        '["structuring","scoring"]',
        '["scoring"]',
        '["scoring","extraction"]',
        '["ekstraksi"]',
        "not json",
    ],
)
def test_an_invalid_sequence_is_422_and_nothing_runs(client, auth, stub_extraction, sequence):
    response = _submit(client, auth, pipeline_name_sequence=sequence)

    assert response.status_code == 422
    body = response.json()
    assert (body["errors"], body["pipeline_last_stage"]) == ("INVALID_PIPELINE_SEQUENCE", "orchestrator")
    assert body["message"].startswith("Invalid pipeline_name_sequence")
    assert stub_extraction.submitted == []


def test_the_sequence_may_be_sent_as_repeated_fields(client, auth, stub_extraction):
    response = client.post(
        "/v1/extract-ocr",
        data={"request_id": "OCR_1", "pipeline_name_sequence": ["extraction", "structuring", "scoring"]},
        files=image_upload("slip_gaji.jpg", JPEG),
        headers=auth,
    )

    assert response.status_code == 200
    assert stub_extraction.submitted[0]["sequence"] == ["extraction", "structuring", "scoring"]


def test_guardrails_only_answers_the_guardrail_report(client, auth, stub_waiter):
    stub_waiter.outcome = WaitOutcome("OCR", "DONE", results={"OCR": OCR_RESULT})

    response = _submit(client, auth, pipeline_name_sequence='["guardrails"]')

    assert response.status_code == 200
    body = response.json()
    assert (body["pipeline_last_stage"], body["guardrails"]) == ("guardrails", 0)
    assert body["data"]["passed"] is True
    assert body["data"]["document"] == ACCEPTED_REPORT["document"]
    assert stub_waiter.sequences == [["guardrails"]]


def test_extraction_only_answers_the_ocr_result(client, auth, stub_waiter):
    stub_waiter.outcome = WaitOutcome("OCR", "DONE", results={"OCR": OCR_RESULT})

    body = _submit(client, auth, pipeline_name_sequence='["extraction"]').json()

    assert body["pipeline_last_stage"] == "extraction"
    assert set(body["data"]) == {"engine", "model", "elapsed_ms", "n_pages", "full_text", "pages"}


def test_ending_at_structuring_answers_the_structured_fields(client, auth, stub_waiter):
    stub_waiter.outcome = WaitOutcome(
        "STRUCTURING", "DONE", results={"OCR": OCR_RESULT, "STRUCTURING": STRUCTURING_RESULT}
    )

    body = _submit(client, auth, pipeline_name_sequence='["extraction","structuring"]').json()

    assert body["pipeline_last_stage"] == "structuring"
    assert body["data"]["total_slip"] == 2
    assert body["data"]["slips"][0]["fields"]["gaji_pokok"] == 4500000


def test_a_document_held_by_a_guardrail_is_400_from_guardrails(client, auth, stub_waiter):
    """Guardrail dinilai di tahap OCR; penolakannya dilaporkan dengan nama service `guardrails`."""
    stub_waiter.outcome = WaitOutcome("GUARDRAILS", STATUS_REJECTED, REJECTED_REASON)

    response = _submit(client, auth)

    assert response.status_code == 400
    body = response.json()
    assert (body["message"], body["errors"]) == (REJECTED_REASON, "DOWNSTREAM_VALIDATION_ERROR")
    assert (body["data"], body["guardrails"], body["pipeline_last_stage"]) == (None, 1, "guardrails")


def test_rejection_by_the_structuring_rules_is_400_from_structuring(client, auth, stub_waiter):
    stub_waiter.outcome = WaitOutcome("STRUCTURING", STATUS_REJECTED, "dokumen tidak punya field wajib")

    body = _submit(client, auth).json()

    assert (body["status_code"], body["guardrails"], body["pipeline_last_stage"]) == (400, 1, "structuring")


def test_params_are_echoed_and_bad_params_are_422(client, auth, stub_extraction):
    ok = _submit(client, auth, params='{"refno": "PK1"}').json()
    bad = _submit(client, auth, params="{not json").json()

    assert ok["params"] == {"refno": "PK1"}
    assert (bad["status_code"], bad["errors"], bad["pipeline_last_stage"]) == (422, "INVALID_PARAMS", "orchestrator")
    assert len(stub_extraction.submitted) == 1


def test_unsupported_document_type_is_400(client, auth, stub_extraction):
    body = _submit(client, auth, document_type="npwp").json()

    assert (body["status_code"], body["errors"], body["pipeline_last_stage"]) == (
        400,
        "UNSUPPORTED_DOCUMENT_TYPE",
        "orchestrator",
    )
    assert stub_extraction.submitted == []


def test_request_id_is_required(client, auth):
    response = client.post("/v1/extract-ocr", files=image_upload("slip_gaji.jpg", JPEG), headers=auth)

    assert response.status_code == 422
    body = response.json()
    assert body["errors"] == "VALIDATION_ERROR"
    assert body["message"] == "body.request_id: Field required"


def test_file_url_is_forwarded_as_url_not_as_bytes(client, auth, monkeypatch, stub_extraction):
    """Tahap OCR mengunduhnya sendiri, juga ketika mengulang job yang ditinggal proses mati."""

    async def fake_fetch(url, *, limit, timeout=10.0, policy):
        assert url == "http://minio.local/bucket/slip_gaji.pdf"
        return JPEG, "slip_gaji.jpg", "image/jpeg"

    monkeypatch.setattr("ocr_common.web.intake.fetch", fake_fetch)

    response = client.post(
        "/v1/extract-ocr",
        data={"request_id": "OCR_4", "file_url": "http://minio.local/bucket/slip_gaji.pdf"},
        headers=auth,
    )

    assert response.status_code == 200
    assert stub_extraction.submitted[0]["file_url"] == "http://minio.local/bucket/slip_gaji.pdf"


def test_unsupported_content_type_is_400_before_anything_starts(client, auth, stub_extraction):
    response = _submit(client, auth, content_type="text/plain")

    assert response.status_code == 400
    assert response.json()["errors"] == "UNSUPPORTED_FILE_TYPE"
    assert stub_extraction.submitted == []


def test_oversized_document_is_413_before_anything_starts(client, auth, settings_override, stub_extraction):
    settings_override(max_upload_bytes=10)

    response = _submit(client, auth)

    assert response.status_code == 413
    body = response.json()
    assert (body["status_desc"], body["errors"]) == ("Payload Too Large", "FILE_TOO_LARGE")
    assert body["message"].startswith("Ukuran dokumen melebihi batas")
    assert stub_extraction.submitted == []


def test_a_three_month_document_is_within_the_page_limit(client, auth, stub_extraction):
    """Bentuk yang paling sering diunggah adalah tiga bulan dalam satu berkas."""
    response = _submit_pdf(client, auth, _pdf(3))

    assert response.status_code == 200
    assert len(stub_extraction.submitted) == 1


def test_more_pages_than_the_limit_is_400_before_anything_starts(client, auth, settings_override, stub_extraction):
    settings_override(max_document_pages=2)

    response = _submit_pdf(client, auth, _pdf(3))

    assert response.status_code == 400
    body = response.json()
    assert (body["message"], body["errors"]) == (TOO_MANY_PAGES, "TOO_MANY_PAGES")
    assert stub_extraction.submitted == []


def test_unreadable_pdf_is_400_before_anything_starts(client, auth, stub_extraction):
    response = _submit_pdf(client, auth, b"%PDF-1.4 garbage")

    assert response.status_code == 400
    assert response.json()["errors"] == "UNREADABLE_FILE"
    assert stub_extraction.submitted == []


def test_missing_api_key_returns_401_envelope(client):
    response = client.post("/v1/extract-ocr", data={"request_id": "OCR_7"}, files=image_upload())

    assert response.status_code == 401
    assert response.json()["errors"] == "UNAUTHORIZED"
