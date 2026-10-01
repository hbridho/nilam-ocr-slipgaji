from app.ml.mock import MockIdentityCheck

URL = "/v1/guardrail/identity/check"
SLIP = "SLIP GAJI OKTOBER 2023\nGaji Pokok Rp 2.400.000\nTunjangan Rp 300.000\nPotongan Rp 100.000"
OTHER = "SURAT KETERANGAN DOMISILI\nYang bertanda tangan di bawah ini"


def test_a_payslip_passes(client, auth, use_check):
    use_check(MockIdentityCheck())

    data = client.post(URL, json={"text": SLIP}, headers=auth).json()["data"]

    assert (data["verdict"], data["passed"], data["reason"]) == ("slip_gaji", True, None)
    assert data["proba_slip_gaji"] >= data["reject_threshold"]


def test_another_document_is_held(client, auth, use_check):
    use_check(MockIdentityCheck())

    data = client.post(URL, json={"text": OTHER}, headers=auth).json()["data"]

    assert (data["verdict"], data["passed"]) == ("bukan_slip_gaji", False)
    assert "bukan slip gaji" in data["reason"]


def test_the_report_names_the_threshold_actually_used(client, auth, use_check):
    """Tanpa ini sebuah putusan tidak bisa dijelaskan setelah kejadian: ambangnya boleh datang dari
    Orkestrasi pusat dan berubah tanpa deploy di sini."""
    use_check(MockIdentityCheck(), threshold=0.9)

    data = client.post(URL, json={"text": SLIP}, headers=auth).json()["data"]

    assert data["reject_threshold"] == 0.9


def test_the_threshold_moves_the_verdict(client, auth, use_check):
    """Yang memutuskan memang ambangnya, bukan sesuatu yang lain: dokumen yang sama, dua ambang.

    Ambangnya dipilih mengapit skor si mock (0,05 untuk surat domisili, 0,99 untuk slip): ambang
    yang sangat longgar meloloskan surat domisili, dan ambang yang lebih ketat dari skor tertinggi
    yang bisa diberi mock menahan slip yang asli."""
    use_check(MockIdentityCheck(), threshold=0.01)
    lenient = client.post(URL, json={"text": OTHER}, headers=auth).json()["data"]["verdict"]

    use_check(MockIdentityCheck(), threshold=0.995)
    strict = client.post(URL, json={"text": SLIP}, headers=auth).json()["data"]["verdict"]

    assert (lenient, strict) == ("slip_gaji", "bukan_slip_gaji")


def test_confidence_is_about_the_verdict_not_about_the_class(client, auth, use_check):
    """Pada dokumen yang ditolak, `confidence` tinggi berarti yakin MENOLAK — bukan yakin slip gaji.

    Dua angka ini pernah tertukar di UI, dan hasilnya penolakan yang terbaca sebagai penerimaan."""
    use_check(MockIdentityCheck())

    data = client.post(URL, json={"text": OTHER}, headers=auth).json()["data"]

    assert data["passed"] is False
    assert data["confidence"] == round(1 - data["proba_slip_gaji"], 4)


def test_the_confidence_summary_is_accepted_and_ignored(client, auth, use_check):
    """Badan permintaan sama untuk ketiga guardrail, supaya orchestrator bisa fan-out satu payload."""
    use_check(MockIdentityCheck())

    without = client.post(URL, json={"text": SLIP}, headers=auth).json()["data"]
    with_conf = client.post(
        URL, json={"text": SLIP, "confidence": {"n_boxes": 84, "mean": 0.1, "min": 0.0, "n_low": 80}}, headers=auth
    ).json()["data"]

    assert without == with_conf


def test_an_empty_text_is_answered_not_refused(client, auth, use_check):
    """Pemeriksaan ini tidak boleh mendahulukan `blank`: itu tugas penggabung di orchestrator."""
    use_check(MockIdentityCheck())

    response = client.post(URL, json={"text": ""}, headers=auth)

    assert response.status_code == 200
    assert response.json()["data"]["check"] == "identity"


def test_the_request_id_is_echoed(client, auth, use_check):
    use_check(MockIdentityCheck())

    body = client.post(URL, json={"text": SLIP, "request_id": "OCR_abc"}, headers=auth).json()

    assert body["request_id"] == "OCR_abc"


def test_text_is_required(client, auth, use_check):
    use_check(MockIdentityCheck())

    assert client.post(URL, json={}, headers=auth).status_code == 422


def test_the_endpoint_needs_the_api_key(client):
    assert client.post(URL, json={"text": SLIP}).status_code == 401
