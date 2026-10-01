"""Tahap OCR: dokumen masuk, teks per halaman keluar, lalu guardrail menilai teks itu.

Urutannya OCR dulu, guardrail sesudahnya, karena model guardrail slip gaji membaca teks OCR.
Konsekuensinya diuji di sini: dokumen yang ditahan tetap menyimpan hasil OCR-nya, dan guardrail yang
mati tidak menghentikan extraction kecuali fail-open dimatikan.
"""

import pytest

from ocr_common.clients.guardrails import GuardrailEndpoint, GuardrailsFanout
from ocr_common.errors import ServiceError, UpstreamUnavailable
from ocr_common.testing import image_upload

from app.config import Settings
from app.ml.mock import MockOcrEngine
from app.services.extraction_service import ExtractionService, ocr_summary

SETTINGS = Settings(api_key="x", _env_file=None)
SLIP_PAGE = "SLIP GAJI\nPeriode  : Februari 2025\nGaji Pokok  Rp 4.500.000"


class StubEngine:
    name = "stub"

    def __init__(self, pages: int = 1, text: str = SLIP_PAGE):
        self._pages = pages
        self._text = text
        self.seen: list[str] = []

    def read(self, filename: str, content: bytes) -> dict:
        self.seen.append(filename)
        # n_boxes ikut: ringkasan mutu menimbang rata-rata dengan jumlah kotak, jadi tanpa itu
        # skornya tidak bisa dijumlahkan antar halaman.
        pages = [
            {"page": i, "text": self._text, "confidence": {"mean": 0.98, "min": 0.9, "n_boxes": 40, "n_low": 1}}
            for i in range(1, self._pages + 1)
        ]
        return {
            "pages": pages,
            "full_text": "\n".join(page["text"] for page in pages),
            "model": "stub-v1",
            "engine": "stub",
            "elapsed_ms": 1.0,
            "n_pages": len(pages),
        }


class StubGuardrails:
    """Ketiga guardrail sebagai satu stub: menjawab laporan gabungan tetap."""

    def __init__(self, verdict: str = "slip_gaji"):
        self.verdict = verdict
        self.seen: list[tuple[str, dict | None, dict | None]] = []

    async def check(self, request_id, text, confidence=None, *, n_pages, thresholds=None) -> dict:
        self.seen.append((text, confidence, thresholds))
        passed = self.verdict == "slip_gaji"
        reasons = {
            "blank": "Dokumen kosong: tidak ada teks yang terbaca. Pastikan halaman yang diunggah benar.",
            "blur": "Dokumen terlalu buram untuk dibaca. Mohon foto ulang dengan lebih jelas.",
            "bukan_slip_gaji": "Dokumen ini bukan slip gaji. Mohon unggah slip gaji.",
        }
        return {
            "passed": passed,
            "reason": None if passed else reasons[self.verdict],
            "verdict": self.verdict,
            "document": {"verdict": "accepted" if passed else "reject", "confidence": 0.99, "n_pages": n_pages},
            "checks": {},
            "skipped": [],
            "unavailable": [],
            "pages": [],
        }


def _service(engine=None, guardrails=None) -> ExtractionService:
    return ExtractionService(engine or StubEngine(), SETTINGS, guardrails)


async def test_pages_are_kept_apart_not_merged():
    """Satu berkas tiga bulan = tiga halaman. Menggabungkan teksnya akan mencampur komponen satu
    bulan ke total bulan lain, jadi `pages` harus tetap terpisah."""
    result = await _service(StubEngine(pages=3)).extract("slip.pdf", "application/pdf", b"%PDF-1.4")

    assert result["n_pages"] == 3
    assert [page["page"] for page in result["pages"]] == [1, 2, 3]
    assert result["full_text"].count("SLIP GAJI") == 3


async def test_the_guardrail_sees_the_ocr_text_the_quality_summary_and_the_thresholds():
    """Ringkasan mutu OCR ikut dikirim: tanpa itu gerbang blur hanya bisa melihat panjang teks."""
    guardrails = StubGuardrails()
    thresholds = {"identity": {"value": 0.8, "target": "accept"}}

    result = await _service(guardrails=guardrails).extract(
        "slip.pdf", "application/pdf", b"%PDF-1.4", request_id="R1", guardrail_thresholds=thresholds
    )

    [(text, confidence, seen_thresholds)] = guardrails.seen
    assert text == SLIP_PAGE
    assert confidence["mean"] == 0.98
    assert seen_thresholds == thresholds
    assert result["guardrails"]["verdict"] == "slip_gaji"
    assert result.get("reject_reason") is None


async def test_the_quality_summary_is_aggregated_over_pages():
    """Halaman berisi 3 kotak tidak boleh menarik rata-rata sekuat halaman berisi 90, dan satu
    halaman yang hancur sudah cukup membuat dokumen tidak terpakai — jadi minimum diambil terburuk."""
    summary = ocr_summary(
        [
            {"confidence": {"n_boxes": 90, "mean": 0.98, "min": 0.40, "n_low": 3}},
            {"confidence": {"n_boxes": 10, "mean": 0.60, "min": 0.10, "n_low": 6}},
        ]
    )

    assert summary == {"n_boxes": 100, "n_low": 9, "mean": 0.942, "min": 0.10}


@pytest.mark.parametrize("verdict", ["bukan_slip_gaji", "blur", "blank"])
async def test_a_held_document_keeps_its_ocr_result_and_carries_a_reject_reason(verdict):
    """Penolakan bukan exception: hasil OCR-nya sah dan tetap disimpan, sehingga penolakan bisa
    ditelusuri tanpa menjalankan ulang OCR. `reject_reason` yang menghentikan pipeline, dan
    alasannya berbeda-beda karena tindakan yang diminta dari nasabah juga berbeda."""
    result = await _service(guardrails=StubGuardrails(verdict=verdict)).extract("x.pdf", "application/pdf", b"%PDF-1.4")

    assert result["guardrails"]["verdict"] == verdict
    assert result["reject_reason"], "dokumen yang ditahan harus membawa alasannya"
    assert result["pages"], "hasil OCR harus tetap ada"


async def test_a_sequence_without_guardrails_does_not_call_them():
    """`pipeline_name_sequence` tanpa `guardrails`: tidak ada yang menilai, dan hasilnya tidak berpura-pura."""
    guardrails = StubGuardrails()

    result = await _service(guardrails=guardrails).extract(
        "x.pdf", "application/pdf", b"%PDF-1.4", run_guardrails=False
    )

    assert guardrails.seen == []
    assert result["guardrails"] is None
    assert result.get("reject_reason") is None


async def test_one_guardrail_down_is_judged_by_the_other_two():
    """Fail-open per guardrail: blur mati, blank dan identity tetap memutuskan."""
    fanout = GuardrailsFanout(
        [
            GuardrailEndpoint("blank", _Answering(BLANK_OK)),
            GuardrailEndpoint("blur", _FailingClient()),
            GuardrailEndpoint("identity", _Answering(ID_OK)),
        ],
        fail_open=True,
    )

    result = await _service(guardrails=fanout).extract("x.pdf", "application/pdf", b"%PDF-1.4")

    assert result["guardrails"]["passed"] is True
    assert result["guardrails"]["unavailable"] == ["blur"]
    assert result.get("reject_reason") is None


async def test_fail_closed_turns_a_guardrail_outage_into_a_failed_job():
    fanout = GuardrailsFanout(
        [
            GuardrailEndpoint("blank", _Answering(BLANK_OK)),
            GuardrailEndpoint("blur", _FailingClient()),
            GuardrailEndpoint("identity", _Answering(ID_OK)),
        ],
        fail_open=False,
    )

    with pytest.raises(ServiceError) as exc:
        await _service(guardrails=fanout).extract("x.pdf", "application/pdf", b"%PDF-1.4")

    assert exc.value.status_code == 503


async def test_all_guardrails_switched_off_still_explains_itself():
    fanout = GuardrailsFanout([GuardrailEndpoint(name, None) for name in ("blank", "blur", "identity")])

    result = await _service(guardrails=fanout).extract("x.pdf", "application/pdf", b"%PDF-1.4")

    assert (result["guardrails"]["passed"], result["guardrails"]["verdict"]) == (True, "skipped")
    assert result["guardrails"]["skipped"] == ["blank", "blur", "identity"]


async def test_a_document_without_text_becomes_a_blank_verdict_not_a_400():
    """Berkas terbaca tapi tidak ada teksnya bukan galat pemanggil: itu putusan `blank`, dan
    nasabah butuh alasannya ("pastikan halaman yang diunggah benar"), bukan kode 400."""
    guardrails = StubGuardrails(verdict="blank")

    result = await _service(StubEngine(text="   "), guardrails).extract("x.pdf", "application/pdf", b"%PDF-1.4")

    assert result["guardrails"]["verdict"] == "blank"
    assert "kosong" in result["reject_reason"].lower()


async def test_the_mock_engine_shape_matches_the_contract():
    result = MockOcrEngine().read("slip_gaji.pdf", b"seed")

    assert set(result) == {"pages", "full_text", "model", "engine", "elapsed_ms", "n_pages"}
    assert all(set(page) == {"page", "text", "confidence"} for page in result["pages"])


async def test_the_mock_engine_can_produce_a_three_month_document():
    assert MockOcrEngine().read("3slip.pdf", b"seed")["n_pages"] == 3


BLANK_OK = {"check": "blank", "verdict": "ok", "passed": True, "reason": None, "chars": 60, "max_chars": 20}
ID_OK = {
    "check": "identity",
    "verdict": "slip_gaji",
    "passed": True,
    "reason": None,
    "proba_slip_gaji": 0.97,
    "reject_threshold": 0.47,
}


class _Answering:
    """Klien HTTP sebuah service guardrail yang menjawab laporan tetap."""

    def __init__(self, report):
        self.name = "guardrail service"
        self._report = report

    async def post_json(self, path, payload, *, headers=None):
        return {"data": self._report}

    async def aclose(self):
        pass


class _FailingClient:
    """Klien HTTP yang selalu gagal seperti service yang mati."""

    name = "guardrail-blur service"

    async def post_json(self, path, payload, *, headers=None):
        raise UpstreamUnavailable("guardrail-blur service is unavailable")

    async def aclose(self):
        pass


# --- lewat HTTP -------------------------------------------------------------------------


def test_http_extract_returns_pages_and_a_guardrail_report(client, auth):
    response = client.post(
        "/v1/extraction/extract", files=image_upload("slip.pdf", b"%PDF-1.4 fake", "application/pdf"), headers=auth
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["engine"] == "mock"
    assert data["n_pages"] == len(data["pages"]) >= 1
    assert data["guardrails"]["skipped"] == ["blank", "blur", "identity"]  # dimatikan di conftest


def test_http_extract_is_deterministic_per_content(client, auth):
    first = client.post("/v1/extraction/extract", files=image_upload(content=b"same"), headers=auth).json()["data"]
    second = client.post("/v1/extraction/extract", files=image_upload(content=b"same"), headers=auth).json()["data"]

    assert first["pages"] == second["pages"]


def test_http_extract_empty_file_returns_400(client, auth):
    response = client.post("/v1/extraction/extract", files=image_upload(content=b""), headers=auth)

    assert response.status_code == 400
    assert response.json()["message"] == "Uploaded file is empty"


def test_http_file_url_is_fetched_by_service(client, auth, monkeypatch):
    async def fake_fetch(url, *, limit, timeout=10.0, policy):
        assert url == "http://minio.local/bucket/slip_gaji.pdf"
        return b"%PDF-1.4 bytes-from-url", "slip_gaji.pdf", "application/pdf"

    monkeypatch.setattr("ocr_common.web.intake.fetch", fake_fetch)

    response = client.post(
        "/v1/extraction/extract", data={"file_url": "http://minio.local/bucket/slip_gaji.pdf"}, headers=auth
    )

    assert response.status_code == 200
    assert response.json()["data"]["engine"] == "mock"


def test_missing_api_key_returns_401_envelope(client):
    response = client.post("/v1/extraction/extract", files=image_upload())

    assert response.status_code == 401
    # `errors` membawa KODE stabil, bukan kalimat: klien bercabang pada kode.
    assert response.json()["errors"] == "UNAUTHORIZED"
