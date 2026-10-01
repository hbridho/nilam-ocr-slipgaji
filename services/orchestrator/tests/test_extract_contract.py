import json

import pytest

from ocr_common.slip_gaji import SLIP_FIELDS, contract_data

from app.config import get_settings
from app.main import app
from tests.conftest import JPEG, scores, slip

RID = "REQ_contract"


def _submit(client, auth, **form):
    return client.post(
        "/v1/extract-ocr",
        headers=auth,
        data={"request_id": RID, **form},
        files={"file": ("slip_gaji.jpg", JPEG, "image/jpeg")},
    )


def _final(slips=None, scored=None):
    return {
        "document_type": "slip_gaji",
        "total_slip": len(slips) if slips is not None else 1,
        "slips": [
            {**entry, "scores": score["scores"]}
            for entry, score in zip(slips or [slip()], scored or [scores()], strict=True)
        ],
        "guardrails": None,
    }


def test_confidence_is_1_from_the_threshold_up():
    data = contract_data(_final(scored=[scores(gaji_pokok=0.8, nama_karyawan=0.799)]), threshold=0.8)

    [entry] = data["slip"]
    assert entry["gaji_pokok"] == {"value": 4500000, "confidence": 1}
    assert entry["nama_karyawan"] == {"value": "ANDI SAPUTRA", "confidence": 0}


def test_a_field_without_a_value_is_null_with_confidence_0():
    [entry] = contract_data(_final(), threshold=0.5)["slip"]

    assert entry["divisi"] == {"value": None, "confidence": 0}


def test_every_slip_carries_all_twenty_fields():
    """Klien menerima 20 kunci yang sama untuk tiap slip, jadi tidak perlu menebak mana yang hilang."""
    [entry] = contract_data(_final(), threshold=0.5)["slip"]

    assert set(SLIP_FIELDS) <= set(entry)
    assert set(entry) == set(SLIP_FIELDS) | {"page", "missing_mandatory_fields"}


def test_a_three_month_document_returns_three_slips_in_page_order():
    slips = [slip(1, 1, "2025-02"), slip(2, 2, "2025-03"), slip(3, 3, "2025-04")]
    data = contract_data(_final(slips, [scores(1), scores(2), scores(3)]), threshold=0.5)

    assert data["total_slip"] == 3
    assert [entry["page"] for entry in data["slip"]] == [1, 2, 3]
    assert [entry["periode"]["value"] for entry in data["slip"]] == ["2025-02", "2025-03", "2025-04"]


def test_a_field_without_a_score_is_confidence_0():
    """Field yang tidak dinilai tahap scoring tidak boleh diam-diam dianggap yakin."""
    data = contract_data(_final(scored=[{"slip_no": 1, "scores": {}}]), threshold=0.5)

    assert data["slip"][0]["gaji_pokok"] == {"value": 4500000, "confidence": 0}


def test_missing_mandatory_fields_are_derived_from_the_values_themselves():
    """Dihitung ulang dari nilainya, bukan disalin dari tahap sebelumnya: daftar yang dioper bisa
    ketinggalan begitu sebuah field diperbaiki di tengah jalan."""
    thin = slip()
    thin["fields"]["nama_perusahaan"] = None
    thin["fields"]["periode"] = ""
    thin["missing_mandatory_fields"] = []  # tahap sebelumnya keliru mengatakan lengkap

    data = contract_data(_final([thin], [scores()]), threshold=0.5)

    assert data["slip"][0]["missing_mandatory_fields"] == ["nama_perusahaan", "periode"]


@pytest.mark.parametrize("params", ['{"nik": "3123456711950001", "refno": "PK19039Y8U"}', '"halo"'])
def test_params_are_returned_unchanged(client, auth, params):
    response = _submit(client, auth, params=params)

    assert response.status_code == 200
    assert response.json()["params"] == json.loads(params)


@pytest.mark.parametrize("params", ["{not json", "[1, 2]", "42"])
def test_invalid_params_are_422_before_anything_runs(client, auth, stub_extraction, params):
    response = _submit(client, auth, params=params)

    assert response.status_code == 422
    body = response.json()
    assert (body["errors"], body["message"]) == (
        "INVALID_PARAMS",
        "params must be valid JSON: an object, or a quoted string",
    )
    assert body["pipeline_last_stage"] == "orchestrator"
    assert stub_extraction.submitted == []


def test_unsupported_document_type_is_400_before_anything_runs(client, auth, stub_extraction):
    response = _submit(client, auth, document_type="ktp")

    assert response.status_code == 400
    body = response.json()
    assert (body["errors"], body["message"]) == (
        "UNSUPPORTED_DOCUMENT_TYPE",
        "Unsupported document_type: ktp. Supported: slip_gaji",
    )
    assert stub_extraction.submitted == []


def test_confidence_threshold_can_be_changed(client, auth):
    """Ambang milik kontrak, bukan model: mengubahnya tidak melatih ulang apa pun."""
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"field_confidence_threshold": 0.95}
    )
    try:
        strict = _submit(client, auth).json()["data"]
    finally:
        app.dependency_overrides.pop(get_settings, None)
    lenient = _submit(client, auth).json()["data"]

    assert strict["slip"][0]["gaji_pokok"]["confidence"] == 0  # 0,936 < 0,95
    assert lenient["slip"][0]["gaji_pokok"]["confidence"] == 1  # 0,936 >= 0,5
