"""Backend yang sebenarnya, langsung — tanpa HTTP. Dilewati kalau berkas model tidak ada di image."""

import pytest

from app.ml.slip_blank import SlipBlankCheck

SLIP = "SLIP GAJI\nOKTOBER 2023\nNama  : RATNA SUSANTI\nGaji Pokok  Rp 2.400.000"


@pytest.fixture(scope="module")
def model():
    pytest.importorskip("slip_ml")
    try:
        return SlipBlankCheck()
    except RuntimeError as exc:  # pragma: no cover - hanya di image tanpa berkas model
        pytest.skip(str(exc))


def test_it_reads_the_limit_from_the_quality_gate(model):
    """Batasnya harus satu angka yang sama dengan yang dipakai saat melatih gerbang mutu: kalau
    berbeda di dua tempat, halaman di perbatasan dinilai kosong oleh yang satu dan buram oleh yang
    lain."""
    assert model.metadata["blank_max_chars"] == 20


def test_a_real_payslip_is_not_blank(model):
    report = model.check(SLIP, None)

    assert (report["verdict"], report["passed"]) == ("ok", True)


def test_whitespace_only_is_blank(model):
    """Halaman yang OCR-nya hanya menghasilkan spasi dan baris baru tetap kosong."""
    report = model.check("   \n\n \t ", None)

    assert (report["verdict"], report["passed"], report["chars"]) == ("blank", False, 0)


def test_the_override_is_reported_as_the_limit_used(model):
    report = model.check("x" * 10, 5)

    assert (report["max_chars"], report["verdict"]) == (5, "ok")
