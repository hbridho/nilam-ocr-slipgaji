from app.ml.mock import MockBlurCheck

URL = "/v1/guardrail/blur/check"
SLIP = "SLIP GAJI\nOKTOBER 2023\nNama  : RATNA SUSANTI\nGaji Pokok  Rp 2.400.000"
GOOD = {"n_boxes": 84, "mean": 0.9812, "min": 0.4123, "n_low": 4}
BAD = {"n_boxes": 84, "mean": 0.42, "min": 0.01, "n_low": 70}


def test_a_readable_page_passes(client, auth, use_check):
    use_check(MockBlurCheck())

    data = client.post(URL, json={"text": SLIP, "confidence": GOOD}, headers=auth).json()["data"]

    assert (data["verdict"], data["passed"], data["reason"]) == ("ok", True, None)
    assert data["ocr_mean"] == 0.9812


def test_a_broken_page_is_held(client, auth, use_check):
    use_check(MockBlurCheck())

    data = client.post(URL, json={"text": SLIP, "confidence": BAD}, headers=auth).json()["data"]

    assert (data["verdict"], data["passed"]) == ("blur", False)
    assert "buram" in data["reason"]


def test_the_report_names_the_threshold_actually_used(client, auth, use_check):
    """Ambang dari setelan menimpa ambang model, dan laporannya mencatat yang benar-benar dipakai —
    tanpa itu, sebuah putusan tidak bisa dijelaskan setelah kejadian."""
    use_check(MockBlurCheck(), blur_threshold=0.5)

    data = client.post(URL, json={"text": SLIP, "confidence": GOOD}, headers=auth).json()["data"]

    assert data["threshold"] == 0.5


def test_an_empty_page_is_reported_as_blank_but_still_judged_here(client, auth, use_check):
    """Pemeriksaan ini tidak boleh mendahulukan `blank`: itu tugas penggabung di orchestrator.

    Yang wajib ada di sini hanyalah petunjuknya, supaya penggabung dan yang membaca log tahu
    halamannya memang tidak punya teks untuk dinilai."""
    use_check(MockBlurCheck())

    data = client.post(URL, json={"text": "", "confidence": GOOD}, headers=auth).json()["data"]

    assert data["blank"] is True
    assert data["check"] == "blur"
    assert data["verdict"] in ("ok", "blur")


def test_without_a_confidence_summary_the_gate_says_so_instead_of_guessing(client, auth, use_check):
    """Empat dari enam ciri datang dari ringkasan skor; tanpa itu semuanya nol — nilai yang sama
    yang diberikan halaman hancur. Diukur pada model asli: teks slip yang bersih tanpa ringkasan
    skor memberi p_broken 0,9999. Menyebut itu `blur` berarti menahan dokumen yang baik dan
    menyuruh penggunanya memfoto ulang sesuatu yang sudah benar."""
    use_check(MockBlurCheck())

    response = client.post(URL, json={"text": SLIP}, headers=auth)
    data = response.json()["data"]

    assert response.status_code == 200
    assert data["verdict"] == "mutu_tak_terukur"
    assert data["p_broken"] is None
    assert data["ocr_mean"] is None
    assert "tidak dikirim" in data["reason"]


def test_the_request_id_is_echoed(client, auth, use_check):
    use_check(MockBlurCheck())

    body = client.post(URL, json={"text": SLIP, "request_id": "OCR_abc"}, headers=auth).json()

    assert body["request_id"] == "OCR_abc"


def test_text_is_required(client, auth, use_check):
    use_check(MockBlurCheck())

    assert client.post(URL, json={"confidence": GOOD}, headers=auth).status_code == 422


def test_the_endpoint_needs_the_api_key(client):
    assert client.post(URL, json={"text": SLIP}).status_code == 401
