"""Penyerahan ke tahap OCR: apa yang benar-benar dikirim orchestrator, dan bagaimana galat tahap itu
sampai ke klien.

Guardrail TIDAK dipanggil dari sini — tahap OCR yang memanggil ketiganya setelah teksnya ada, karena
modelnya membaca teks OCR. Yang diteruskan: urutan service dan ambang permintaan, sudah dinormalkan.
"""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from ocr_common.clients.remote import RemoteModelClient
from ocr_common.slip_gaji import SLIP_FIELDS

from app.clients.extraction import ExtractionJobClient
from app.dependencies import get_extraction_client
from app.main import app
from tests.conftest import JPEG

RID = "REQ_orchestrator_jobs"


def _accepted(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        202,
        json={
            "status_code": 202,
            "status_desc": "Accepted",
            "message": "Accepted",
            "data": {"request_id": RID, "stage": "OCR", "status": "PROCESSING", "duplicate": False},
            "errors": None,
            "request_id": RID,
        },
    )


class Extraction:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(response, Exception):
            raise response
        return response(request) if callable(response) else response


@pytest.fixture
def extraction():
    def install(*responses) -> Extraction:
        handler = Extraction(*responses)
        remote = RemoteModelClient(
            "http://extraction:8030",
            5.0,
            name="extraction service",
            headers={"X-API-Key": "k"},
            passthrough_client_errors=True,
            transport=httpx.MockTransport(handler),
        )
        app.dependency_overrides[get_extraction_client] = lambda: ExtractionJobClient(remote, attempts=3, delay=0)
        return handler

    yield install
    app.dependency_overrides.pop(get_extraction_client, None)


def _submit(client, auth, filename="slip_gaji.jpg"):
    return client.post(
        "/v1/extract-ocr",
        headers=auth,
        data={"request_id": RID, "document_type": "slip_gaji"},
        files={"file": (filename, JPEG, "image/jpeg")},
    )


def _form(request: httpx.Request) -> tuple[dict[str, str], bytes]:
    content_type = request.headers["content-type"]
    boundary = content_type.split("boundary=")[1].encode()
    fields: dict[str, str] = {}
    file_bytes = b""
    for part in request.content.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, body = part.split(b"\r\n\r\n", 1)
        body = body.removesuffix(b"\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        if b"filename=" in head:
            file_bytes = body
        else:
            fields[name] = body.decode()
    return fields, file_bytes


def test_the_document_is_handed_to_the_ocr_stage(client, auth, extraction):
    handler = extraction(_accepted)

    response = _submit(client, auth)

    assert response.status_code == 200
    body = response.json()
    assert (body["status_code"], body["guardrails"]) == (200, 0)

    [sent] = handler.requests
    assert sent.url.path == "/v1/extraction/jobs"
    assert sent.headers["X-API-Key"] == "k"
    fields, file_bytes = _form(sent)
    assert fields["request_id"] == RID
    assert fields["document_type"] == "slip_gaji"
    assert "guardrails" not in fields, "laporan guardrail dihasilkan tahap OCR, bukan dikirim dari sini"
    assert "pipeline_name_sequence" not in fields, "tanpa urutan berarti pipeline penuh"
    assert file_bytes == JPEG


def test_the_sequence_and_thresholds_travel_normalised(client, auth, extraction):
    handler = extraction(_accepted)

    response = client.post(
        "/v1/extract-ocr",
        headers=auth,
        data={
            "request_id": RID,
            "pipeline_name_sequence": '["guardrails","extraction","structuring","scoring"]',
            "guardrails_confidence_threshold": '{"acc_rej": 0.8}',
            "column_confidence_threshold": '{"all_field": 0.6}',
        },
        files={"file": ("slip_gaji.jpg", JPEG, "image/jpeg")},
    )

    assert response.status_code == 200
    [sent] = handler.requests
    fields, file_bytes = _form(sent)
    assert json.loads(fields["pipeline_name_sequence"]) == ["guardrails", "extraction", "structuring", "scoring"]
    assert json.loads(fields["guardrails_confidence_threshold"]) == {"identity": 0.8}
    # all_field is spread over every slip field at the entry point (upstream 57cc691).
    assert json.loads(fields["column_confidence_threshold"]) == dict.fromkeys(SLIP_FIELDS, 0.6)
    assert (fields["request_id"], file_bytes) == (RID, JPEG)


def test_extraction_unreachable_is_503(client, auth, extraction):
    extraction(httpx.ConnectError("refused"))

    response = _submit(client, auth)

    assert response.status_code == 503
    assert response.json()["message"] == "extraction service is unavailable"


def test_extraction_client_error_is_passed_through(client, auth, extraction):
    extraction(httpx.Response(400, json={"detail": "Uploaded file is empty"}))

    response = _submit(client, auth)

    assert response.status_code == 400
    assert response.json()["message"] == "Uploaded file is empty"


def test_extraction_server_error_is_retried(client, auth, extraction):
    handler = extraction(httpx.Response(502, text="bad gateway"), _accepted)

    response = _submit(client, auth)

    assert response.status_code == 200
    assert len(handler.requests) == 2


def test_a_document_sent_as_file_url_is_handed_over_as_the_same_url(client, auth, extraction, monkeypatch):
    """Byte-nya tidak ikut: tahap OCR mengunduhnya sendiri, juga ketika ia mengulang job yang
    ditinggal proses mati — jadi URL harus tetap berlaku lebih lama daripada sewa job."""
    handler = extraction(_accepted)
    url = "http://minio.local/bucket/slip_gaji.pdf?X-Amz-Signature=abc"

    async def fake_fetch(fetched, *, limit, timeout=10.0, policy):
        assert fetched == url
        return JPEG, "slip_gaji.jpg", "image/jpeg"

    monkeypatch.setattr("ocr_common.web.intake.fetch", fake_fetch)
    response = client.post(
        "/v1/extract-ocr", headers=auth, data={"request_id": RID, "document_type": "slip_gaji", "file_url": url}
    )

    assert response.status_code == 200
    [sent] = handler.requests
    assert sent.url.path == "/v1/extraction/jobs"
    assert sent.headers["content-type"] == "application/x-www-form-urlencoded", "tanpa byte: tahap OCR yang mengunduh"
    form = {key: value[0] for key, value in parse_qs(sent.content.decode()).items()}
    assert form["request_id"] == RID
    assert form["file_url"] == url
