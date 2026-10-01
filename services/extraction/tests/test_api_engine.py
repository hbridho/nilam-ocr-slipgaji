"""Backend OCR bawaan: layanan HTTP, bukan model di dalam image.

Yang diuji di sini adalah apa yang dikirim ke layanan OCR, bagaimana jawabannya menjadi teks per
halaman, dan bagaimana kegagalannya menjadi status yang bisa ditindaklanjuti pemanggil.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import fitz
import pytest

from ocr_common.errors import BadRequest, InternalError, UpstreamUnavailable

from app.ml.api import ApiOcrEngine

# Satu halaman dengan tiga kotak: judul, lalu label di kiri dan nominal di kanan pada baris yang sama.
# Pasangan kiri-kanan itulah bentuk slip gaji, dan fitur layout guardrail membacanya dari dua spasi
# yang dihasilkan langkah penyusunan urutan baca.
REPLY = {
    "num_pages": 1,
    "pages": [
        {
            "texts": [
                {"text": "SLIP GAJI", "score": 0.99, "poly": [[100, 10], [300, 10], [300, 40], [100, 40]]},
                {"text": "Gaji Pokok", "score": 0.98, "poly": [[40, 80], [200, 80], [200, 110], [40, 110]]},
                {"text": "Rp 4.500.000", "score": 0.97, "poly": [[400, 80], [600, 80], [600, 110], [400, 110]]},
            ]
        }
    ],
}


def _pdf(n_pages: int = 1) -> bytes:
    document = fitz.open()
    for i in range(n_pages):
        document.new_page(width=300, height=200).insert_text((20, 40), f"halaman {i + 1}")
    return document.tobytes()


class _Handler(BaseHTTPRequestHandler):
    reply = REPLY
    status = 200
    seen: list[dict] = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).seen.append({"path": self.path, "is_png": b"\x89PNG" in body, "field": b'name="file"' in body})
        payload = json.dumps(type(self).reply).encode() if type(self).status == 200 else b"nope"
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def ocr_service():
    """Layanan OCR tiruan; kembalikan (url, handler) supaya tes bisa mengatur jawabannya."""

    class Handler(_Handler):
        reply = REPLY
        status = 200
        seen = []

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", Handler
    server.shutdown()
    server.server_close()


def test_every_page_is_sent_to_the_ocr_service_as_an_image(ocr_service):
    url, handler = ocr_service

    result = ApiOcrEngine(url, timeout=10).read("slip_gaji.pdf", _pdf(3))

    assert result["engine"] == "api"
    assert result["n_pages"] == 3
    assert len(handler.seen) == 3, "satu panggilan per halaman"
    assert handler.seen[0] == {"path": "/ocr", "is_png": True, "field": True}


def test_the_reply_is_rebuilt_into_reading_order(ocr_service):
    """Kotak yang sebaris digabung dengan dua spasi — itulah yang dibaca aturan structuring dan
    fitur layout guardrail, dan itu tidak boleh bergantung pada mesin OCR mana yang dipakai."""
    url, _ = ocr_service

    result = ApiOcrEngine(url, timeout=10).read("slip_gaji.pdf", _pdf(1))

    assert result["pages"][0]["text"] == "SLIP GAJI\nGaji Pokok  Rp 4.500.000"


def test_the_ocr_scores_of_the_service_are_kept(ocr_service):
    """Tahap scoring membaca porsi kotak berskor rendah sebagai fitur mutu OCR."""
    url, _ = ocr_service

    confidence = ApiOcrEngine(url, timeout=10).read("slip_gaji.pdf", _pdf(1))["pages"][0]["confidence"]

    assert confidence["n_boxes"] == 3
    assert confidence["min"] == 0.97
    assert confidence["n_low"] == 0


def test_the_endpoint_and_field_name_are_configurable(ocr_service):
    url, handler = ocr_service

    ApiOcrEngine(url, endpoint="/v2/ocr", upload_field="file", timeout=10).read("slip_gaji.pdf", _pdf(1))

    assert handler.seen[0]["path"] == "/v2/ocr"


def test_a_page_limit_keeps_a_fat_pdf_from_eating_a_worker(ocr_service):
    url, handler = ocr_service

    result = ApiOcrEngine(url, timeout=10, max_pages=2).read("slip_gaji.pdf", _pdf(5))

    assert (result["n_pages"], len(handler.seen)) == (2, 2)


def test_an_unreachable_service_is_503_not_a_crash():
    engine = ApiOcrEngine("http://127.0.0.1:1", timeout=2)  # tidak ada yang mendengarkan

    with pytest.raises(UpstreamUnavailable) as exc:
        engine.read("slip_gaji.pdf", _pdf(1))

    assert exc.value.status_code == 503


def test_a_server_error_from_the_service_is_503(ocr_service):
    url, handler = ocr_service
    handler.status = 503

    with pytest.raises(UpstreamUnavailable):
        ApiOcrEngine(url, timeout=10).read("slip_gaji.pdf", _pdf(1))


def test_a_client_error_from_the_service_is_500_because_retrying_will_not_help(ocr_service):
    """404 berarti permintaan KITA yang salah bentuk (endpoint keliru), bukan layanan yang sibuk."""
    url, handler = ocr_service
    handler.status = 404

    with pytest.raises(InternalError) as exc:
        ApiOcrEngine(url, endpoint="/salah", timeout=10).read("slip_gaji.pdf", _pdf(1))

    assert "EXTRACTION_OCR" in exc.value.message


def test_an_unreadable_document_is_400_before_the_service_is_called(ocr_service):
    url, handler = ocr_service

    with pytest.raises(BadRequest):
        ApiOcrEngine(url, timeout=10).read("rusak.pdf", b"%PDF-1.4 garbage")

    assert handler.seen == [], "dokumen rusak tidak dikirim ke layanan OCR"


def test_the_engine_refuses_to_start_without_a_url():
    with pytest.raises(RuntimeError, match="EXTRACTION_OCR_URL"):
        ApiOcrEngine("")
