from app.ml.mock import MockBlankCheck

URL = "/v1/guardrail/blank/check"
SLIP = "SLIP GAJI\nOKTOBER 2023\nNama  : RATNA SUSANTI\nGaji Pokok  Rp 2.400.000"


def test_a_page_with_text_passes(client, auth, use_check):
    use_check(MockBlankCheck())

    body = client.post(URL, json={"text": SLIP}, headers=auth).json()

    assert body["data"]["verdict"] == "ok"
    assert body["data"]["passed"] is True
    assert body["data"]["reason"] is None
    assert body["data"]["chars"] == len(SLIP)


def test_an_empty_page_is_blank_and_not_an_error(client, auth, use_check):
    """Teks kosong adalah jawaban, bukan galat: 200 dengan verdict blank."""
    use_check(MockBlankCheck())

    response = client.post(URL, json={"text": ""}, headers=auth)

    assert response.status_code == 200
    assert response.json()["data"]["verdict"] == "blank"
    assert response.json()["data"]["passed"] is False
    assert "tidak ada teks" in response.json()["data"]["reason"]


def test_the_boundary_belongs_to_blank(client, auth, use_check):
    """`chars <= max_chars` berarti kosong — tepat di batas ikut kosong, bukan lolos."""
    use_check(MockBlankCheck())

    at = client.post(URL, json={"text": "x" * 20}, headers=auth).json()["data"]
    over = client.post(URL, json={"text": "x" * 21}, headers=auth).json()["data"]

    assert (at["verdict"], over["verdict"]) == ("blank", "ok")


def test_the_configured_limit_overrides_the_models(client, auth, use_check):
    """BLANK_MAX_CHARS menimpa batas model, dan laporan mencatat batas yang benar-benar dipakai."""
    use_check(MockBlankCheck(), blank_max_chars=5)

    data = client.post(URL, json={"text": "x" * 10}, headers=auth).json()["data"]

    assert data["max_chars"] == 5
    assert data["verdict"] == "ok"


def test_the_confidence_summary_is_accepted_and_ignored(client, auth, use_check):
    """Badan permintaan sama untuk ketiga guardrail, supaya orchestrator bisa fan-out satu payload.

    Di sini `confidence` tidak boleh mengubah apa pun: yang diputuskan hanya panjang teks."""
    use_check(MockBlankCheck())

    without = client.post(URL, json={"text": SLIP}, headers=auth).json()["data"]
    with_conf = client.post(
        URL, json={"text": SLIP, "confidence": {"n_boxes": 84, "mean": 0.1, "min": 0.0, "n_low": 80}}, headers=auth
    ).json()["data"]

    assert without == with_conf


def test_the_request_id_is_echoed(client, auth, use_check):
    use_check(MockBlankCheck())

    body = client.post(URL, json={"text": SLIP, "request_id": "OCR_abc"}, headers=auth).json()

    assert body["request_id"] == "OCR_abc"


def test_text_is_required(client, auth, use_check):
    use_check(MockBlankCheck())

    assert client.post(URL, json={}, headers=auth).status_code == 422


def test_the_endpoint_needs_the_api_key(client):
    assert client.post(URL, json={"text": SLIP}).status_code == 401
