"""Endpoint scoring sinkron, dan model keyakinan yang benar-benar dikirim di image.

Model dilatih pada 1.428 nilai dari 50 dokumen berlabel; out-of-fold AUC 0,826 · Gini 0,652 ·
ECE 0,037. Yang diuji di sini bukan mutu modelnya (itu diukur di repo penelitian), melainkan bahwa
service menyajikan skornya utuh: skala 0-1, hanya untuk field yang ada nilainya, tanpa keputusan.
"""

import pytest

from ocr_common.slip_gaji import contract_data

from app.ml.mock import MockConfidenceModel
from app.services.confidence_service import ConfidenceService
from slip_ml import confidence

PAGE = "SLIP GAJI\nPeriode  : Februari 2025\nGaji Pokok  Rp 4.500.000\nTOTAL PENDAPATAN  Rp 5.100.000"
SLIP = {
    "slip_no": 1,
    "page": 1,
    "fields": {
        "nama_perusahaan": "PT SUMBER REJEKI MAKMUR",
        "periode": "2025-02",
        "gaji_pokok": 4500000,
        "total_pendapatan": 5100000,
        "divisi": None,
    },
    "source": {"gaji_pokok": {"how": "regex", "label": "gaji pokok", "line": "Gaji Pokok  Rp 4.500.000"}},
    "checks": {"earnings": ["ok"]},
    "counts": {"filled": 4, "by_regex": 4, "by_derived": 0, "by_llm": 0, "chars": len(PAGE)},
    "llm": {"compare": {}},
    "ocr_text": PAGE,
    "ocr_confidence": {"mean": 0.98, "n_boxes": 40, "n_low": 2},
    "missing_mandatory_fields": [],
}


def test_scores_are_on_the_0_1_scale_and_skip_empty_fields():
    result = ConfidenceService(MockConfidenceModel(), 0.5).score({"slips": [SLIP]})

    scores = result["slips"][0]["scores"]
    assert all(0 <= value <= 1 for value in scores.values()), "skala 0-1, sama dengan API spec [07]"
    assert "divisi" not in scores
    assert result["threshold"] == 0.5


def test_the_service_makes_no_decision():
    """Pemetaan ke confidence 0/1 milik kontrak extract-ocr, supaya ambangnya bisa diubah tanpa
    melatih ulang apa pun."""
    result = ConfidenceService(MockConfidenceModel(), 0.5).score({"slips": [SLIP]})

    assert set(result) == {"slips", "threshold", "model"}
    assert all(set(entry) == {"slip_no", "scores"} for entry in result["slips"])


def _direct(client, auth, **body):
    return client.post("/v1/scoring-direct", json={"structuring": {"slips": [SLIP]}, **body}, headers=auth)


def test_scoring_direct_scores_a_structured_document_and_records_nothing(client, auth):
    response = _direct(client, auth, request_id="QC_1")

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "QC_1"
    data = body["data"]
    assert data["slips"][0]["slip_no"] == 1
    assert 0 < data["slips"][0]["scores"]["gaji_pokok"] <= 1
    [slip] = data["data"]["slip"]
    assert slip["gaji_pokok"]["value"] == 4500000
    assert slip["gaji_pokok"]["confidence"] == int(data["slips"][0]["scores"]["gaji_pokok"] >= 0.5)
    assert client.get("/v1/scoring/jobs/QC_1", headers=auth).status_code == 404


def test_scoring_direct_column_threshold_decides_the_0_1_confidences(client, auth):
    data = _direct(client, auth, column_confidence_threshold={"all_field": 1}).json()["data"]

    assert all(field["confidence"] == 0 for field in data["data"]["slip"][0].values() if isinstance(field, dict))
    assert data["column_confidence_threshold"]["gaji_pokok"] == 1.0, "all_field disebar ke setiap field"


def test_scoring_direct_another_document_type_is_400(client, auth):
    response = _direct(client, auth, document_type="npwp")

    assert response.status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"structuring": {"slips": "x"}},
        {"structuring": {"slips": [SLIP]}, "column_confidence_threshold": {"nomor_npwp": 0.9}},
    ],
)
def test_scoring_direct_bad_body_or_unknown_threshold_key_is_422(client, auth, body):
    response = client.post("/v1/scoring-direct", json=body, headers=auth)

    assert response.status_code == 422
    assert response.json()["errors"] == "VALIDATION_ERROR"


def test_requires_api_key(client):
    assert client.post("/v1/scoring-direct", json={"structuring": {"slips": [SLIP]}}).status_code == 401


# --- model yang benar-benar dikirim ------------------------------------------------------

pytestmark_model = pytest.mark.skipif(confidence.model_info() is None, reason="conf.json tidak ada di image")


@pytestmark_model
def test_the_shipped_model_scores_a_real_slip():
    scores = confidence.score_slip(SLIP)

    assert set(scores) <= set(SLIP["fields"])
    assert "divisi" not in scores
    assert all(0 <= value <= 1 for value in scores.values()), "skala 0-1, sama dengan API spec [07]"


@pytestmark_model
def test_the_shipped_model_maps_scores_to_the_0_1_contract():
    scores = confidence.score_slip(SLIP)
    [data] = contract_data({"slips": [{"page": 1, "fields": SLIP["fields"], "scores": scores}]}, 0.8)["slip"]

    assert set(data) >= set(SLIP["fields"])
    assert data["divisi"] == {"value": None, "confidence": 0}
    for name in SLIP["fields"]:
        if data[name]["value"] is not None:
            assert data[name]["confidence"] == (1 if scores.get(name, 0) >= 0.8 else 0)


@pytestmark_model
def test_a_higher_threshold_can_only_remove_confident_fields():
    scores = confidence.score_slip(SLIP)
    slips = {"slips": [{"page": 1, "fields": SLIP["fields"], "scores": scores}]}
    [lenient] = contract_data(slips, 0.5)["slip"]
    [strict] = contract_data(slips, 0.99)["slip"]
    [per_field] = contract_data(slips, 0.5, {"all_field": 0.99})["slip"]

    def confident(slip):
        return sum(slip[name]["confidence"] for name in SLIP["fields"])

    assert confident(strict) <= confident(lenient)
    assert confident(per_field) == confident(strict), "all_field menimpa ambang bawaan untuk setiap field"


@pytestmark_model
def test_the_shipped_model_reports_what_it_was_trained_on():
    info = confidence.model_info()

    assert info["n_rows"] > info["n_errors"] > 0
    assert 0.5 < info["auc"] < 1.0
    assert len(info["features"]) == 9  # 9 variabel + one-hot field; berubahnya berarti model lain
