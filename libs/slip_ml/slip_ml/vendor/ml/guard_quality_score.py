#!/usr/bin/env python3
"""Sajikan gerbang mutu. numpy saja — tanpa sklearn, tanpa pickle.

    import guard_quality_score as Q
    Q.score(text, ocr_confidence)     # -> 0..1, peluang halaman ini terlalu rusak untuk dipakai
    Q.verdict(text, ocr_confidence)   # -> "blank" | "blur" | "ok"

Ciri yang dibaca sama persis dengan sisi latih (guard4_fit.quality_features): skor OCR rata-rata,
skor minimum, porsi kotak berskor < 0,90, jumlah kotak, jumlah karakter, karakter per kotak.
Tidak ada satu kata pun yang dibaca — itulah sebabnya gerbang ini tidak peduli dokumennya berbunyi
apa, dan tetap bekerja pada dokumen yang belum pernah dilihatnya.
"""

import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "models" / "guard_quality.json"
_CACHE = {}

VERDICT_BLANK = "blank"
VERDICT_BLUR = "blur"
VERDICT_OK = "ok"


def load(path=None):
    """Model terlatih, atau None kalau belum pernah diekspor."""
    key = str(path or MODEL_PATH)
    if key not in _CACHE:
        p = Path(key)
        if not p.is_file():
            _CACHE[key] = None
        else:
            m = json.loads(p.read_text(encoding="utf-8"))
            m["_mean"] = np.array(m["mean"])
            m["_scale"] = np.array(m["scale"])
            m["_coef"] = np.array(m["coef"])
            _CACHE[key] = m
    return _CACHE[key]


def features(text: str, confidence: dict | None) -> list[float]:
    """Enam ciri mutu. Disalin dari sisi latih dengan sengaja sesingkat mungkin supaya kedua
    sisi bisa dibandingkan sebaris demi sebaris; guard4_fit.quality_features adalah acuannya."""
    confidence = confidence or {}
    n_boxes = float(confidence.get("n_boxes") or 0)
    mean = confidence.get("mean")
    low = float(confidence.get("n_low") or 0)
    chars = float(len(text or ""))
    return [
        min(chars / 4000.0, 1.0),
        min(n_boxes / 200.0, 1.0),
        # Halaman kosong tidak punya skor sama sekali; 0 membedakannya dari halaman berskor buruk.
        float(mean) if mean is not None else 0.0,
        float(confidence.get("min") or 0.0),
        low / n_boxes if n_boxes else 0.0,
        min((chars / n_boxes) / 40.0, 1.0) if n_boxes else 0.0,
    ]


def is_blank(text: str, model=None) -> bool:
    """Halaman tanpa teks yang berarti. Aturan, bukan model: tidak ada yang perlu ditaksir di sini,
    dan sebuah model yang dilatih atas halaman kosong buatan akan terlihat sempurna tanpa
    mengajari apa pun."""
    limit = (model or load() or {}).get("blank_max_chars", 20)
    return len((text or "").strip()) <= limit


def score(text: str, confidence: dict | None = None, model=None) -> float | None:
    """Peluang halaman ini terlalu rusak untuk diekstraksi; None kalau model belum diekspor."""
    m = model or load()
    if m is None:
        return None
    x = (np.asarray(features(text, confidence), dtype=np.float64) - m["_mean"]) / m["_scale"]
    return 1.0 / (1.0 + math.exp(-(float(x @ m["_coef"]) + m["intercept"])))


def threshold(model=None) -> float:
    m = model or load()
    return float((m or {}).get("threshold", 0.9))


def verdict(text: str, confidence: dict | None = None, model=None) -> str:
    """`blank`, `blur`, atau `ok`. Urutannya penting: halaman kosong diputus lebih dulu, karena
    tanpa teks tidak ada mutu yang bisa diukur."""
    m = model or load()
    if is_blank(text, m):
        return VERDICT_BLANK
    p = score(text, confidence, m)
    if p is None:
        return VERDICT_OK
    return VERDICT_BLUR if p >= threshold(m) else VERDICT_OK
