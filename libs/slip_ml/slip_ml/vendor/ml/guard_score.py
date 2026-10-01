#!/usr/bin/env python3
"""Sajikan guardrail slip gaji. numpy saja — tanpa sklearn, tanpa pickle.

    import guard_score as GS
    GS.score_text(ocr_text)        # -> 0..1, peluang berkas ini slip gaji (dari teks OCR)
    GS.score_pix(path)             # -> 0..1, dari piksel saja
    GS.decide(p, kind="text")      # -> True kalau lolos ambang yang diekspor

    python scripts/guard_score.py BERKAS.pdf     # skor piksel + teks (kalau hasil OCR ada)

Kedua model dilatih guard_fit.py dan dibaca dari models/guard_text.json dan guard_pix.json.
Featurisasinya diimpor dari guard_features — modul yang SAMA dengan sisi latih.
"""

import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import guard_features as GF  # noqa: E402

MODELS = HERE / "models"
_CACHE = {}


def load(kind: str):
    """Model terlatih, atau None kalau belum pernah diekspor."""
    if kind not in _CACHE:
        p = MODELS / f"guard_{kind}.json"
        if not p.is_file():
            _CACHE[kind] = None
        else:
            m = json.loads(p.read_text(encoding="utf-8"))
            m["_coef"] = np.array(m["coef"])
            if kind == "text":
                m["_index"] = {t: i for i, t in enumerate(m["vocab"])}
                m["_idf"] = np.array(m["idf"])
            else:
                m["_mean"], m["_scale"] = np.array(m["mean"]), np.array(m["scale"])
            _CACHE[kind] = m
    return _CACHE[kind]


def _calibrate(m, z):
    a, b = m["platt"]
    return 1.0 / (1.0 + math.exp(-(a * z + b)))


def score_text(text: str):
    """Peluang slip gaji dari teks OCR seluruh halaman. Baris pembungkus BRISPOT dibuang
    di sini juga, jadi teks mentah dari pipeline boleh langsung diberikan."""
    m = load("text")
    if m is None:
        return None
    text = GF.strip_wrapper(text)
    z = m["intercept"]
    counts = Counter(g for g in GF.text_grams(text, m["ngram_max"]) if g in m["_index"])
    if counts:
        idx = np.fromiter((m["_index"][g] for g in counts), dtype=np.int64)
        tf = np.fromiter(counts.values(), dtype=np.float64)
        if m.get("sublinear_tf"):
            tf = 1.0 + np.log(tf)
        x = tf * m["_idf"][idx]
        norm = np.linalg.norm(x)
        if norm > 0:
            x /= norm
        z += float(x @ m["_coef"][idx])
    dn = m.get("dense")
    if dn:  # layout baris dari teks yang sama
        import guard_struct as GST

        v = np.asarray(GST.text_layout_features(text), dtype=np.float64)
        d = np.nan_to_num((v - np.array(dn["mean"])) / np.array(dn["scale"])) * dn["w"]
        z += float(d @ np.array(dn["coef"]))
    return _calibrate(m, z)


def score_pix_vector(vec):
    m = load("pix")
    if m is None:
        return None
    x = (np.asarray(vec, dtype=np.float64) - m["_mean"]) / m["_scale"]
    return _calibrate(m, float(x @ m["_coef"]) + m["intercept"])


def score_pix(path):
    return score_pix_vector(GF.pixel_features(Path(path)))


def decide(p, kind="text"):
    """True = lolos guardrail (dianggap slip gaji) pada ambang yang diekspor bersama model."""
    m = load(kind)
    return None if (m is None or p is None) else bool(p >= m["threshold"])


def _ocr_text_for(path: Path):
    out = HERE.parent / "ocr_main" / "output"
    stem = path.stem
    for b in sorted(p for p in out.iterdir() if p.is_dir()) if out.is_dir() else []:
        files = sorted(b.glob(f"{stem}_p*.detail.json")) or sorted(b.glob(f"{stem}.detail.json"))
        texts = [json.loads(f.read_text(encoding="utf-8")).get("ocr_text") or "" for f in files]
        if any(texts):
            return "\n".join(texts)
    return None


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        p = Path(arg)
        print(p.name)
        pp = score_pix(p)
        if pp is not None:
            print(f"   piksel : {pp:.3f}  -> {'LOLOS' if decide(pp, 'pix') else 'DITAHAN'}")
        t = _ocr_text_for(p)
        if t is None:
            print("   teks   : belum ada hasil OCR untuk berkas ini")
        else:
            pt = score_text(t)
            print(f"   teks   : {pt:.3f}  -> {'LOLOS' if decide(pt, 'text') else 'DITAHAN'}")
