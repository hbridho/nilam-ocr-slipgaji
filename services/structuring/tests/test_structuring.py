"""Structuring: teks OCR per halaman menjadi 20 field slip gaji.

Aturan extractionnya di-vendor dari repo penelitian (`slip_ml`), jadi yang diuji di sini adalah
kontrak tahapnya: satu halaman menjadi satu slip, nominal keluar sebagai integer, jejak yang
dibutuhkan tahap scoring ikut terbawa, dan halaman kosong ditolak.
"""

import pytest

from ocr_common.errors import ServiceError
from ocr_common.slip_gaji import MANDATORY_FIELDS, SLIP_FIELDS
from ocr_common.types import OcrPage

from app.ml.slip_rules import SlipRulesStructurer
from app.services.structuring_service import StructuringService

PAGE_ONE = """PT SUMBER REJEKI MAKMUR
SLIP GAJI
Periode  : Februari 2025
Nama  : ANDI SAPUTRA
NIK  : 202309001
Jabatan  : Staff Gudang
PENGHASILAN
Gaji Pokok  Rp 4.500.000
Tunjangan Makan  Rp 600.000
TOTAL PENDAPATAN  Rp 5.100.000
POTONGAN
TOTAL POTONGAN  Rp 225.000
GAJI BERSIH  Rp 4.875.000
"""
PAGE_TWO = PAGE_ONE.replace("Februari 2025", "Maret 2025").replace("4.500.000", "4.700.000")

PAGES: list[OcrPage] = [
    {"page": 1, "text": PAGE_ONE, "confidence": {"mean": 0.98}},
    {"page": 2, "text": PAGE_TWO, "confidence": {"mean": 0.97}},
]


def _structure(pages=None):
    return StructuringService(SlipRulesStructurer()).structure(pages if pages is not None else PAGES)


def test_one_page_becomes_one_slip():
    """Berkas tiga bulan adalah bentuk yang paling sering diunggah; menggabungkan halamannya akan
    mencampur komponen satu bulan ke total bulan lain."""
    result = _structure()

    assert result["n_slips"] == 2
    assert [slip["page"] for slip in result["slips"]] == [1, 2]
    assert [slip["slip_no"] for slip in result["slips"]] == [1, 2]


def test_every_slip_has_all_twenty_fields():
    """Klien selalu menerima 20 kunci yang sama, jadi tidak perlu menebak field mana yang hilang."""
    [slip, _] = _structure()["slips"]

    assert set(slip["fields"]) == set(SLIP_FIELDS)


def test_money_comes_out_as_integers_not_formatted_text():
    fields = _structure()["slips"][0]["fields"]

    assert fields["gaji_pokok"] == 4500000
    assert fields["total_pendapatan"] == 5100000
    assert fields["gaji_bersih"] == 4875000


def test_each_slip_keeps_its_own_period():
    first, second = _structure()["slips"]

    assert first["fields"]["periode"] != second["fields"]["periode"]
    assert first["fields"]["gaji_pokok"] == 4500000
    assert second["fields"]["gaji_pokok"] == 4700000


def test_the_trace_the_scoring_model_needs_is_carried_along():
    """`source`, `checks`, `counts`, `llm.compare`, teks dan skor OCR halaman bukan hiasan: fitur
    terkuat model keyakinan justru berasal dari jejak itu."""
    slip = _structure()["slips"][0]

    assert slip["source"], "asal tiap nilai harus ikut"
    assert slip["ocr_text"].startswith("PT SUMBER REJEKI MAKMUR")
    assert slip["ocr_confidence"] == {"mean": 0.98}
    assert set(slip["counts"]) >= {"filled", "by_regex", "by_derived", "by_llm"}
    assert "compare" in slip["llm"]


def test_missing_mandatory_fields_are_listed_per_slip():
    thin = [{"page": 1, "text": "SLIP GAJI\nGaji Pokok  Rp 4.500.000", "confidence": {}}]

    [slip] = _structure(thin)["slips"]

    assert "gaji_pokok" not in slip["missing_mandatory_fields"]
    assert set(slip["missing_mandatory_fields"]) <= set(MANDATORY_FIELDS)
    assert "nama_karyawan" in slip["missing_mandatory_fields"]


def test_the_llm_layer_is_off_by_default():
    """Service harus bisa berjalan tanpa kredensial AWS; tanpa arbitrase akurasinya 81,5% lawan 89,9%."""
    result = _structure()

    assert result["llm_used"] is False
    assert SlipRulesStructurer().use_llm is False


def test_blank_pages_are_rejected():
    with pytest.raises(ServiceError) as exc:
        _structure([{"page": 1, "text": "   ", "confidence": {}}])

    assert exc.value.status_code == 400


def test_pages_without_text_are_skipped_not_counted():
    mixed = [{"page": 1, "text": "   ", "confidence": {}}, {"page": 2, "text": PAGE_ONE, "confidence": {}}]

    result = _structure(mixed)

    assert result["n_slips"] == 1
    assert result["slips"][0]["page"] == 2


# --- lewat HTTP -------------------------------------------------------------------------


def test_health_lists_backend(client):
    assert client.get("/health").json()["backends"] == {"structuring": "slip_rules", "storage": "memory"}


def _direct(client, auth, pages, **body):
    return client.post("/v1/structuring-direct", json={"ocr": {"pages": pages}, **body}, headers=auth)


def test_structuring_direct_structures_an_ocr_result_and_records_nothing(client, auth):
    response = _direct(client, auth, PAGES, request_id="QC_1")

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "QC_1"
    data = body["data"]
    assert data["document_type"] == "slip_gaji"
    assert data["n_slips"] == 2
    assert data["slips"][0]["fields"]["nama_karyawan"] == "ANDI SAPUTRA"
    assert data["reject_reason"] is None
    assert client.get("/v1/structuring/jobs/QC_1", headers=auth).status_code == 404


def test_structuring_direct_blank_pages_returns_400(client, auth):
    response = _direct(client, auth, [{"page": 1, "text": " "}])

    assert response.status_code == 400
    assert response.json()["message"] == "tidak ada teks OCR yang bisa distrukturkan"


def test_structuring_direct_another_document_type_is_400(client, auth):
    response = _direct(client, auth, PAGES, document_type="npwp")

    assert response.status_code == 400
    assert response.json()["message"] == "Unsupported document_type: npwp. Supported: ['slip_gaji']"


def test_validation_error_uses_envelope_with_code(client, auth):
    response = client.post("/v1/structuring-direct", json={"pages": PAGES}, headers=auth)

    assert response.status_code == 422
    body = response.json()
    assert body["errors"] == "VALIDATION_ERROR"
    assert body["message"].startswith("body.ocr:")


def test_requires_api_key(client):
    assert client.post("/v1/structuring-direct", json={"ocr": {"pages": PAGES}}).status_code == 401
