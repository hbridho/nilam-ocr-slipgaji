"""Guardrail: apakah berkas ini slip gaji?

Dua model, keduanya dilatih di repo penelitian (scripts/guard_fit.py) dan disajikan dengan
numpy saja:

    teks    TF-IDF unigram+bigram + 8 fitur layout baris, atas teks OCR.
            AUC 0,908 · pada ambang yang dipakai 41 dari 51 dokumen bukan-slip tertahan
            dan 97,2% slip asli lolos.
    piksel  35 fitur OpenCV atas halaman yang dirender. AUC 0,638 — jauh lebih lemah,
            dipakai HANYA ketika teks OCR belum ada.

Model teks butuh teks OCR, jadi guardrail slip gaji berjalan SETELAH tahap OCR, bukan
sebelumnya seperti guardrail berbasis gambar. Itu sebabnya `check_text` adalah jalur utama.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from slip_ml.vendor import ensure_path

ensure_path()

import guard_quality_score as _quality  # noqa: E402
import guard_score as _guard  # noqa: E402

VERDICT_ACCEPTED = "accepted"
VERDICT_REJECT = "reject"

KIND_TEXT = "text"
KIND_PIX = "pix"

# Empat kemungkinan jawaban guardrail. Tiga yang pertama menahan dokumen, masing-masing dengan
# tindakan berbeda bagi pengguna: unggah halaman yang benar · foto ulang lebih jelas · unggah
# dokumen yang benar.
VERDICT_BLANK = "blank"
VERDICT_BLUR = "blur"
VERDICT_WRONG_DOCUMENT = "bukan_slip_gaji"
VERDICT_SLIP_GAJI = "slip_gaji"

# Hanya muncul ketika ketiga pemeriksaan dipanggil sebagai service terpisah dan salah satunya tidak
# menjawab. Kaskade `check()` tidak pernah menghasilkannya: di satu proses tidak ada penjaga yang bisu.
VERDICT_INCOMPLETE = "guardrail_tidak_lengkap"

# Gerbang mutu tanpa ringkasan skor OCR. Dibedakan dari `blur` dengan sengaja: keduanya menahan
# dokumen, tetapi yang satu temuan tentang dokumennya dan yang lain kekurangan pada masukannya —
# dan yang kedua diperbaiki dengan mengirim ringkasan skor, bukan dengan memfoto ulang.
VERDICT_UNMEASURED = "mutu_tak_terukur"

REASONS = {
    VERDICT_BLANK: "Dokumen kosong: tidak ada teks yang terbaca. Pastikan halaman yang diunggah benar.",
    VERDICT_BLUR: "Dokumen terlalu buram untuk dibaca. Mohon foto ulang dengan lebih jelas.",
    VERDICT_WRONG_DOCUMENT: "Dokumen ini bukan slip gaji. Mohon unggah slip gaji.",
    VERDICT_INCOMPLETE: "Dokumen belum bisa diputuskan: pemeriksaan {checks} tidak menjawab. Mohon coba lagi.",
    VERDICT_UNMEASURED: (
        "Mutu halaman tidak bisa diukur: ringkasan skor OCR (`mean`, `min`, `n_boxes`, `n_low`) "
        "tidak dikirim. Ini kekurangan pada permintaan, bukan temuan tentang dokumennya."
    ),
}


class GuardrailUnavailable(RuntimeError):
    """Model guardrail tidak ada di image — dibedakan dari dokumen yang ditolak."""


def model_info(kind: str = KIND_TEXT) -> dict[str, Any] | None:
    """Ringkasan model terlatih, atau None kalau berkasnya tidak ada."""
    model = _guard.load(kind)
    if model is None:
        return None
    return {
        "kind": model.get("kind"),
        "threshold": model.get("threshold"),
        "n_train": model.get("n_train"),
        "n_yes": model.get("n_yes"),
        "n_no": model.get("n_no"),
        "cv_auc": model.get("cv_auc"),
        "vocab": len(model.get("vocab") or ()),
        "layout_features": len((model.get("dense") or {}).get("names") or ()),
    }


def threshold(kind: str = KIND_TEXT) -> float:
    model = _guard.load(kind)
    if model is None:
        raise GuardrailUnavailable(f"model guardrail '{kind}' tidak tersedia")
    return float(model["threshold"])


def check_text(text: str, *, reject_threshold: float | None = None) -> dict[str, Any]:
    """Laporan guardrail dari teks OCR seluruh halaman.

    `reject_threshold` menimpa ambang yang ikut tersimpan bersama model — orchestrator boleh
    memiliki ambangnya sendiri, dan laporan selalu mencatat ambang yang benar-benar dipakai.
    """
    probability = _guard.score_text(text or "")
    if probability is None:
        raise GuardrailUnavailable("model guardrail 'text' tidak tersedia")
    return _report(
        float(probability), reject_threshold if reject_threshold is not None else threshold(KIND_TEXT), KIND_TEXT
    )


def check_file(path: str | Path, *, reject_threshold: float | None = None) -> dict[str, Any]:
    """Laporan guardrail dari piksel — hanya untuk berkas yang belum di-OCR."""
    probability = _guard.score_pix(Path(path))
    if probability is None:
        raise GuardrailUnavailable("model guardrail 'pix' tidak tersedia")
    return _report(
        float(probability), reject_threshold if reject_threshold is not None else threshold(KIND_PIX), KIND_PIX
    )


def check(
    text: str, confidence: dict[str, Any] | None = None, *, reject_threshold: float | None = None
) -> dict[str, Any]:
    """Putusan guardrail lengkap untuk satu dokumen: blank · blur · bukan_slip_gaji · slip_gaji.

    Tiga pemeriksaan berurutan, dan urutannya bukan selera:

      1. **kosong** — tanpa teks, tidak ada mutu maupun identitas yang bisa dinilai.
      2. **mutu** — halaman yang terlalu rusak dinilai dari ciri mutu OCR saja (skor, porsi kotak
         buruk, jumlah karakter). Menanyakan identitas pada teks yang sudah hancur hanya akan
         menghasilkan tebakan yang terdengar yakin.
      3. **identitas** — baru di sini model teks memutuskan slip gaji atau bukan.

    Ketiganya sengaja model/aturan terpisah. Diukur: menyatukannya jadi satu model empat kelas
    menjatuhkan precision bin 2 identitas dari 90% ke 55-70%, sementara menambahkan TF-IDF kata ke
    gerbang mutu hanya menaikkan AUC 0,989 -> 0,992. Keterbacaan dan identitas memang dua
    pertanyaan berbeda.
    """
    quality_model = _quality.load()
    chars = len((text or "").strip())
    ocr_mean = (confidence or {}).get("mean")
    quality_verdict = _quality.verdict(text, confidence, quality_model)
    p_broken = _quality.score(text, confidence, quality_model)
    quality_report = {
        "verdict": quality_verdict,
        "p_broken": round(p_broken, 4) if p_broken is not None else None,
        "threshold": round(_quality.threshold(quality_model), 4) if quality_model else None,
        "chars": chars,
        "ocr_mean": round(float(ocr_mean), 4) if ocr_mean is not None else None,
    }

    # Tanpa ringkasan skor OCR, gerbang mutu tidak mengukur apa pun: keenam cirinya nol, persis
    # seperti halaman yang hancur, jadi `blur` di situ bukan temuan melainkan masukan yang tidak
    # ada. Halaman kosong tetap diputuskan lebih dulu — itu aturan panjang teks, bukan skor.
    if not _quality.is_blank(text, quality_model) and not measurable(confidence):
        return {
            "verdict": VERDICT_UNMEASURED,
            "passed": False,
            "reason": REASONS[VERDICT_UNMEASURED],
            "quality": {**quality_report, "verdict": VERDICT_UNMEASURED, "p_broken": None},
            "identity": None,
        }

    if quality_verdict in (_quality.VERDICT_BLANK, _quality.VERDICT_BLUR):
        verdict = VERDICT_BLANK if quality_verdict == _quality.VERDICT_BLANK else VERDICT_BLUR
        return {
            "verdict": verdict,
            "passed": False,
            "reason": REASONS[verdict],
            "quality": quality_report,
            "identity": None,
        }

    identity = check_text(text, reject_threshold=reject_threshold)
    verdict = VERDICT_SLIP_GAJI if identity["passed"] else VERDICT_WRONG_DOCUMENT
    return {
        "verdict": verdict,
        "passed": identity["passed"],
        "reason": None if identity["passed"] else REASONS[VERDICT_WRONG_DOCUMENT],
        "quality": quality_report,
        "identity": identity["document"],
    }


# --- tiga pemeriksaan, masing-masing bisa dijawab sendiri --------------------------------------
#
# `check()` di atas adalah kaskadenya: kosong -> mutu -> identitas, dengan hubungan pendek. Tiga
# fungsi di bawah membelahnya, supaya tiap pemeriksaan bisa berdiri sebagai service sendiri dan
# ketiganya dipanggil PARALEL. Modelnya sama, ambangnya sama, jawabannya sama; yang hilang hanya
# hubungan pendeknya — pemeriksaan mutu dan identitas tetap menjawab meski halamannya kosong.
#
# Karena itu urutan tetap berlaku, hanya pindah tempat: yang menggabungkan (orchestrator) memakai
# `combine()` di bawah, yang mendahulukan kosong atas buram atas identitas. Menanyakan identitas
# pada teks yang hancur tetap menghasilkan tebakan yang terdengar yakin — bedanya sekarang
# tebakan itu dibuang di penggabung, bukan tak pernah diminta.


def check_blank(text: str) -> dict[str, Any]:
    """Pemeriksaan 1 — kosong. Aturan satu baris, tanpa model dan tanpa ambang dari luar."""
    model = _quality.load()
    chars = len((text or "").strip())
    blank = _quality.is_blank(text, model)
    return {
        "check": VERDICT_BLANK,
        "verdict": VERDICT_BLANK if blank else _quality.VERDICT_OK,
        "passed": not blank,
        "reason": REASONS[VERDICT_BLANK] if blank else None,
        "chars": chars,
        "max_chars": int((model or {}).get("blank_max_chars", 20)),
    }


def measurable(confidence: dict[str, Any] | None) -> bool:
    """Ada ringkasan skor OCR yang cukup untuk mengukur mutu halaman?

    Empat dari enam ciri gerbang mutu datang dari ringkasan ini (`mean`, `min`, `n_boxes`,
    `n_low`). Tanpa satu pun dari mereka, `features()` memberi 0 untuk semuanya — dan nol adalah
    nilai yang sama yang diberikan halaman yang benar-benar hancur. Jadi tanpa ringkasan itu model
    tidak menjawab "buram", ia hanya mengulang masukan yang tidak ada.
    """
    confidence = confidence or {}
    return confidence.get("mean") is not None or bool(confidence.get("n_boxes"))


def check_blur(text: str, confidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pemeriksaan 2 — mutu. Regresi logistik atas 6 ciri mutu OCR; tidak membaca satu kata pun.

    Tanpa ringkasan skor OCR, jawabannya `tak_terukur` — bukan `buram`. Diukur: teks slip yang
    bersih tanpa ringkasan skor memberi p_broken 0,9999, karena ciri-cirinya nol persis seperti
    halaman yang hancur. Menyebut itu "buram" berarti menahan dokumen yang baik dan menyuruh
    penggunanya memfoto ulang sesuatu yang sudah benar.

    `blank` ikut dilaporkan tetapi tidak memutuskan apa pun di sini: pada halaman tanpa teks model
    ini memang berkata "buram", dan yang berhak mendahulukan "kosong" adalah penggabung.
    """
    model = _quality.load()
    if model is None:
        raise GuardrailUnavailable("model gerbang mutu tidak tersedia")
    chars = len((text or "").strip())
    ocr_mean = (confidence or {}).get("mean")
    limit = _quality.threshold(model)
    blank = _quality.is_blank(text, model)

    if not measurable(confidence):
        return {
            "check": VERDICT_BLUR,
            "verdict": VERDICT_UNMEASURED,
            "passed": False,
            "reason": REASONS[VERDICT_UNMEASURED],
            "p_broken": None,
            "threshold": round(limit, 4),
            "chars": chars,
            "ocr_mean": None,
            "blank": blank,
        }

    p_broken = _quality.score(text, confidence, model)
    blur = p_broken is not None and p_broken >= limit
    return {
        "check": VERDICT_BLUR,
        "verdict": VERDICT_BLUR if blur else _quality.VERDICT_OK,
        "passed": not blur,
        "reason": REASONS[VERDICT_BLUR] if blur else None,
        "p_broken": round(p_broken, 4) if p_broken is not None else None,
        "threshold": round(limit, 4),
        "chars": chars,
        "ocr_mean": round(float(ocr_mean), 4) if ocr_mean is not None else None,
        "blank": blank,
    }


def check_identity(text: str, *, reject_threshold: float | None = None) -> dict[str, Any]:
    """Pemeriksaan 3 — identitas. TF-IDF kata + 8 ciri tata letak: slip gaji, atau dokumen lain."""
    report = check_text(text, reject_threshold=reject_threshold)
    document = report["document"]
    passed = bool(report["passed"])
    return {
        "check": "identity",
        "verdict": VERDICT_SLIP_GAJI if passed else VERDICT_WRONG_DOCUMENT,
        "passed": passed,
        "reason": None if passed else REASONS[VERDICT_WRONG_DOCUMENT],
        "proba_slip_gaji": document["proba_slip_gaji"],
        "confidence": document["confidence"],
        "reject_threshold": document["reject_threshold"],
        "model": document["model"],
    }


# Urutan putusan ketika ketiga pemeriksaan sudah menjawab. Sama dengan urutan kaskade di `check()`.
PRECEDENCE = (VERDICT_BLANK, VERDICT_BLUR, "identity")


def combine(reports: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """Satu putusan dokumen dari tiga jawaban terpisah — bentuknya sama dengan `check()`.

    `reports` dikunci `blank` / `blur` / `identity`; None berarti pemeriksaannya tidak menjawab
    (service mati, atau habis waktu). Pemeriksaan yang tidak menjawab dicatat di `unavailable` dan
    TIDAK dianggap lolos: sebuah penjaga yang bisu bukan izin.
    """
    unavailable = [name for name in PRECEDENCE if reports.get(name) is None]
    for name in PRECEDENCE:
        report = reports.get(name)
        if report is not None and not report["passed"]:
            return {
                "verdict": report["verdict"],
                "passed": False,
                "reason": report["reason"],
                "checks": reports,
                "unavailable": unavailable,
            }
    if unavailable:
        # Bukan `slip_gaji` dengan passed=false: itu dua pernyataan yang saling membantah dalam satu
        # laporan. Dokumen ini belum diputuskan, dan itulah yang dikatakan.
        return {
            "verdict": VERDICT_INCOMPLETE,
            "passed": False,
            "reason": REASONS[VERDICT_INCOMPLETE].format(checks=", ".join(unavailable)),
            "checks": reports,
            "unavailable": unavailable,
        }
    return {
        "verdict": VERDICT_SLIP_GAJI,
        "passed": True,
        "reason": None,
        "checks": reports,
        "unavailable": [],
    }


def quality_info() -> dict[str, Any] | None:
    """Ringkasan gerbang mutu, atau None kalau berkas modelnya tidak ada."""
    model = _quality.load()
    if model is None:
        return None
    return {
        "threshold": model.get("threshold"),
        "cv_auc": model.get("cv_auc"),
        "n_train": model.get("n_train"),
        "n_broken": model.get("n_broken"),
        "features": list(model.get("features") or ()),
        "blank_max_chars": model.get("blank_max_chars"),
    }


def _report(probability: float, reject_threshold: float, kind: str) -> dict[str, Any]:
    passed = probability >= reject_threshold
    document = {
        "verdict": VERDICT_ACCEPTED if passed else VERDICT_REJECT,
        "proba_slip_gaji": round(probability, 4),
        "confidence": round(probability if passed else 1.0 - probability, 4),
        "reject_threshold": round(float(reject_threshold), 4),
        "model": kind,
    }
    return {
        "passed": passed,
        "reason": None if passed else _reason(document),
        "document": document,
    }


def _reason(document: dict[str, Any]) -> str:
    return (
        f"Dokumen ditolak guardrail: peluang slip gaji {document['proba_slip_gaji']:.2f} "
        f"di bawah ambang {document['reject_threshold']:.2f} (model {document['model']})"
    )
