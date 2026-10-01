"""Backend yang sebenarnya, langsung — tanpa HTTP. Dilewati kalau berkas model tidak ada di image."""

import pytest

from app.ml.slip_blur import SlipBlurCheck

SLIP = (
    "SLIP GAJI\nOKTOBER 2023\nNama  : RATNA SUSANTI\nGaji Pokok  Rp 2.400.000\n"
    "Total Pendapatan Rp 2.400.000\nGaji Bersih Rp 2.100.000"
)
GOOD = {"n_boxes": 84, "mean": 0.98, "min": 0.41, "n_low": 4}
BAD = {"n_boxes": 9, "mean": 0.31, "min": 0.02, "n_low": 8}


@pytest.fixture(scope="module")
def model():
    pytest.importorskip("slip_ml")
    try:
        return SlipBlurCheck()
    except RuntimeError as exc:  # pragma: no cover - hanya di image tanpa berkas model
        pytest.skip(str(exc))


def test_it_serves_the_six_quality_features(model):
    """Ciri yang dibaca harus sama persis dengan sisi latih — enam, dan tidak satu kata pun."""
    assert len(model.metadata["features"]) == 6


def test_a_clean_page_passes(model):
    report = model.check(SLIP, GOOD, None)

    assert (report["verdict"], report["passed"]) == ("ok", True)
    assert report["p_broken"] < report["threshold"]


def test_a_page_with_poor_ocr_scores_is_held(model):
    report = model.check("Sl1P G4J|\nN4m4 RA7NA", BAD, None)

    assert report["verdict"] == "blur"
    assert report["p_broken"] >= report["threshold"]


def test_the_threshold_override_is_reported(model):
    report = model.check(SLIP, GOOD, 0.001)

    assert report["threshold"] == 0.001
    assert report["verdict"] == "blur"  # ambang yang mustahil menahan halaman yang bersih


def test_a_blank_page_is_flagged_but_not_decided_here(model):
    report = model.check("", GOOD, None)

    assert report["blank"] is True
    assert report["check"] == "blur"


def test_without_a_confidence_summary_nothing_is_measured(model):
    """Ini yang membuat perbaikannya kelihatan: teks slip yang bersih, tanpa ringkasan skor OCR,
    memberi p_broken 0,9999 pada model — nol di empat ciri persis seperti halaman yang hancur.
    Jawabannya harus mengatakan masukannya kurang, bukan menyebut dokumennya buram."""
    report = model.check(SLIP, None, None)

    assert report["verdict"] == "mutu_tak_terukur"
    assert report["p_broken"] is None
    assert report["passed"] is False
