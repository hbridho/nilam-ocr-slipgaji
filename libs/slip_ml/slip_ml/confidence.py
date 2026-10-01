"""Tahap scoring: skor keyakinan per field.

P(nilai = benar | x) dari regresi logistik 9 variabel + one-hot field, dikalibrasi isotonic.
Out-of-fold (GroupKFold per dokumen, 1.428 nilai dari 50 dokumen): AUC 0,826 · Gini 0,652 ·
ECE 0,037. Pada ambang 0,80: precision 93,4%, recall 75,6%.

**Skalanya 0-1** (API spec NILAM [07]). `conf_score` yang di-vendor menghitung 0-100 — itu skala
halaman evaluasi penelitian — dan dibagi 100 tepat di sini, di batas antara kode penelitian dan
service, supaya berkas yang di-vendor tetap identik dengan sumbernya (`scripts/sync_ml.py`).

Featurisasinya TIDAK ditulis ulang di sini — `conf_score` yang di-vendor adalah berkas yang
sama persis dengan yang dipakai saat melatih, dan ia menyusun vektornya menurut NAMA kolom
yang tersimpan di model, bukan indeks tetap.
"""

from __future__ import annotations

from typing import Any

from slip_ml.fields import ALL_FIELDS, blank
from slip_ml.vendor import ensure_path

ensure_path()

import conf_score as _conf  # noqa: E402

# Probabilitas 0-1 yang memisahkan confidence 1 dari 0 di kontrak API bila Orkestrasi pusat tidak
# mengirim `column_confidence_threshold`. 0,5 seperti NPWP; service menimpanya lewat
# FIELD_CONFIDENCE_THRESHOLD.
DEFAULT_SCORE_THRESHOLD = 0.5
# Skala keluaran `conf_score.score_detail` yang di-vendor.
_VENDOR_SCALE = 100.0


class ConfidenceUnavailable(RuntimeError):
    """Model keyakinan tidak ada di image."""


def model_info() -> dict[str, Any] | None:
    model = _conf.load()
    if model is None:
        return None
    metrics = model.get("metrics") or {}
    return {
        "trained": model.get("trained"),
        "variant": model.get("variant"),
        "auc": metrics.get("auc"),
        "gini": metrics.get("gini"),
        "n_rows": metrics.get("n_rows"),
        "n_errors": metrics.get("n_errors"),
        "features": list(model.get("selected") or ()),
    }


def score_slip(slip: dict[str, Any]) -> dict[str, float]:
    """{field: P(nilai benar), 0-1} untuk satu slip hasil `slip_ml.structure.structure_pages`.

    Hanya field yang ada nilainya yang diberi skor: tidak ada yang perlu dinilai pada field
    kosong, dan memberinya 0 akan tercampur dengan nilai yang benar-benar diragukan.
    """
    model = _conf.load()
    if model is None:
        raise ConfidenceUnavailable("model keyakinan (conf.json) tidak tersedia")
    values = {name: slip.get("fields", {}).get(name) for name in ALL_FIELDS}
    detail = {
        "fields": values,
        "source": slip.get("source") or {},
        "checks": slip.get("checks") or {},
        "counts": slip.get("counts") or {},
        "ocr_text": slip.get("ocr_text") or "",
        "ocr_confidence": slip.get("ocr_confidence") or {},
        "llm": {"compare": (slip.get("llm") or {}).get("compare") or {}},
    }
    scores = _conf.score_detail(detail, model=model)
    return {
        name: round(float(score) / _VENDOR_SCALE, 4) for name, score in scores.items() if not blank(values.get(name))
    }
