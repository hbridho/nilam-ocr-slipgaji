"""request_id dari Orkestrasi pusat menjadi X-Request-ID setiap panggilan keluar orchestrator, jadi
satu id mengikuti permintaan melewati log seluruh tahap."""

import httpx

from ocr_common.clients.remote import RemoteModelClient
from ocr_common.web.request_id import REQUEST_ID_HEADER

from app.clients.extraction import ExtractionJobClient
from app.config import get_settings
from app.dependencies import get_extraction_client
from app.main import app
from tests.conftest import JPEG

RID = "OCR_request_id"


def test_calls_carry_the_request_id_of_the_form_not_of_the_header(client, auth):
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get(REQUEST_ID_HEADER))
        return httpx.Response(
            202, json={"data": {"request_id": RID, "stage": "OCR", "status": "PROCESSING", "duplicate": False}}
        )

    remote = RemoteModelClient(
        "http://extraction:8030", 5.0, name="extraction service", transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_extraction_client] = lambda: ExtractionJobClient(remote, attempts=1, delay=0)
    try:
        response = client.post(
            "/v1/extract-ocr",
            data={"request_id": RID},
            files={"file": ("slip_gaji.jpg", JPEG, "image/jpeg")},
            headers={**auth, REQUEST_ID_HEADER: "REQ_gateway_hop"},
        )
    finally:
        app.dependency_overrides.pop(get_extraction_client, None)

    assert response.status_code == 200
    assert seen == [RID]


def test_an_error_raised_in_the_handler_answers_with_the_callers_request_id(client, auth):
    """413 (atau galat apa pun yang muncul setelah form dibaca) harus membawa request_id Orkestrasi
    pusat, bukan milik middleware: hanya id itu yang bisa mereka cocokkan dengan jawabannya."""
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update={"max_upload_bytes": 10})
    try:
        response = client.post(
            "/v1/extract-ocr",
            data={"request_id": RID},
            files={"file": ("slip_gaji.jpg", JPEG, "image/jpeg")},
            headers={**auth, REQUEST_ID_HEADER: "REQ_gateway_hop"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 413
    assert response.json()["request_id"] == RID
    assert response.headers[REQUEST_ID_HEADER] == RID
