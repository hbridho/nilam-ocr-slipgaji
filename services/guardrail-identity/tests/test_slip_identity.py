"""Backend yang sebenarnya, langsung — tanpa HTTP. Dilewati kalau berkas model tidak ada di image."""

import pytest

from app.ml.slip_identity import SlipIdentityCheck

SLIP = (
    "SLIP GAJI\nOKTOBER 2023\nNama  : RATNA SUSANTI\nJabatan : STAFF\n"
    "Gaji Pokok  Rp 2.400.000\nTunjangan Jabatan Rp 300.000\nTotal Pendapatan Rp 2.700.000\n"
    "Potongan BPJS Rp 100.000\nGaji Bersih Rp 2.600.000"
)
OTHER = (
    "SURAT KETERANGAN DOMISILI\nYang bertanda tangan di bawah ini Kepala Desa\n"
    "menerangkan bahwa yang bersangkutan berdomisili di wilayah ini\n"
    "Demikian surat keterangan ini dibuat untuk dipergunakan sebagaimana mestinya"
)


@pytest.fixture(scope="module")
def model():
    pytest.importorskip("slip_ml")
    try:
        return SlipIdentityCheck()
    except RuntimeError as exc:  # pragma: no cover - hanya di image tanpa berkas model
        pytest.skip(str(exc))


def test_the_threshold_comes_from_the_checkpoint(model):
    assert 0 < model.reject_threshold < 1


def test_a_real_payslip_is_recognised(model):
    report = model.check(SLIP, model.reject_threshold)

    assert (report["verdict"], report["passed"]) == ("slip_gaji", True)
    assert report["proba_slip_gaji"] >= model.reject_threshold


def test_another_document_is_held(model):
    report = model.check(OTHER, model.reject_threshold)

    assert (report["verdict"], report["passed"]) == ("bukan_slip_gaji", False)


def test_the_threshold_given_is_the_one_reported(model):
    report = model.check(SLIP, 0.9999)

    assert report["reject_threshold"] == 0.9999
    assert report["verdict"] == "bukan_slip_gaji"  # ambang yang mustahil menahan slip asli


def test_confidence_is_about_the_verdict_not_about_the_class(model):
    held = model.check(OTHER, model.reject_threshold)

    assert held["confidence"] == round(1 - held["proba_slip_gaji"], 4)
