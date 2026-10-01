#!/usr/bin/env python3
"""Sajikan skor keyakinan per field. numpy saja — tanpa sklearn, tanpa pickle.

    import conf_score
    conf_score.score_detail(detail_dict)     # -> {field: 0..100}

Featurisasinya TIDAK diduplikasi di sini: fungsinya diimpor dari conf_build, yang sama
persis dipakai saat melatih. Model lama pernah punya dua salinan featuriser dan keduanya
diam-diam bergeser; satu sumber menghilangkan seluruh kelas bug itu.
"""

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ocr_main"))

import conf_build as CB  # noqa: E402
from core.fields import ALL_FIELDS, blank  # noqa: E402

MODEL_PATH = HERE / "models" / "conf.json"
_CACHE = {}


def load(path=None):
    """Model terlatih, atau None kalau belum pernah dilatih (UI harus diam, bukan error)."""
    p = Path(path or MODEL_PATH)
    key = str(p)
    if key not in _CACHE:
        if not p.is_file():
            _CACHE[key] = None
        else:
            m = json.loads(p.read_text(encoding="utf-8"))
            bg = m["bigram"]
            m["_lm"] = {"bi": bg["bi"], "uni": bg["uni"], "v": bg["v"]}
            m["_coef"] = np.array(m["coef"])
            m["_iso_x"] = np.array(m["iso_x"])
            m["_iso_y"] = np.array(m["iso_y"])
            _CACHE[key] = m
    return _CACHE[key]


def _page_context(detail, values):
    """Konteks halaman, lewat fungsi yang SAMA dengan sisi latih."""
    text = detail.get("ocr_text") or ""
    oc = detail.get("ocr_confidence") or {}
    counts = detail.get("counts") or {}
    return CB.page_context(
        text,
        [ln for ln in text.splitlines() if ln.strip()],
        detail.get("llm") or {},
        oc if isinstance(oc, dict) else {},
        detail.get("checks") or {},
        counts,
        counts.get("filled") or 0,
        values,
    )


def score_detail(detail, values=None, model=None, with_raw=False):
    """{field: 0..100} untuk satu halaman. {} kalau model belum dilatih.

    `values` boleh diberikan kalau nilai yang dinilai berbeda dari yang ada di detail JSON
    (mis. hasil run yang lebih baru); default memakai `detail["fields"]`.

    `with_raw` mengembalikan {field: [skor, proba_mentah]} alih-alih {field: skor}. Proba
    mentah dipakai HANYA sebagai pemecah seri saat mengurutkan: kalibrasi isotonic itu
    fungsi tangga, dan 1.428 nilai hanya menempati 83 skor berbeda — tanpa pemecah seri,
    bin yang dipotong menurut peringkat akan membelah kelompok kembar secara sembarang.
    """
    m = model or load()
    if m is None:
        return {}
    values = values if values is not None else (detail.get("fields") or {})
    source = detail.get("source") or {}
    compare = (detail.get("llm") or {}).get("compare") or {}
    page = _page_context(detail, values)

    # Vektor disusun menurut NAMA kolom yang tersimpan di model, bukan menurut indeks tetap.
    # Dengan begitu mengubah daftar fitur yang dilatih tidak bisa menggeser kolom di sisi
    # saji tanpa ketahuan — model yang menjelaskan dirinya sendiri.
    cols = m["columns"]
    core = [c for c in cols if c not in ALL_FIELDS]
    fitted = {"lm": m["_lm"], "medians": m.get("medians") or {}, "priors": m.get("priors") or {}}
    out = {}
    for field in ALL_FIELDS:
        value = values.get(field)
        if blank(value):
            continue  # tidak ada nilai untuk dinilai
        src = dict(source.get(field) or {})
        src["_agree"] = (compare.get(field) or {}).get("agree")
        feats = CB.fill_fitted(CB.row_features(field, value, src, page), field, value, src.get("line") or "", fitted)

        x = np.zeros(len(cols))
        for j, c in enumerate(core):
            x[j] = float(feats[c])
        if m.get("with_fields") and field in ALL_FIELDS:
            x[len(core) + ALL_FIELDS.index(field)] = 1.0

        z = float(x @ m["_coef"] + m["intercept"])
        p = 1.0 / (1.0 + np.exp(-z))
        # Kalibrasi isotonic disimpan sebagai titik tangga; np.interp memutar ulang fungsi
        # yang sama tanpa perlu sklearn saat serving.
        cal = float(np.interp(p, m["_iso_x"], m["_iso_y"]))
        out[field] = [round(cal * 100, 2), round(p, 6)] if with_raw else round(cal * 100, 2)
    return out


def score_file(path, model=None):
    p = Path(path)
    if not p.is_file():
        return {}
    return score_detail(json.loads(p.read_text(encoding="utf-8")), model=model)


def info():
    """Ringkas untuk ditampilkan di UI / CLI, atau None.

    Termasuk daftar fitur beserta bobot dan penjelasannya, supaya halaman labeling bisa
    menerangkan skornya tanpa siapa pun perlu membuka kode.
    """
    m = load()
    if m is None:
        return None
    met = m.get("metrics") or {}
    cols, coef = m.get("columns") or [], m.get("coef") or []
    w = dict(zip(cols, coef, strict=False))
    note = m.get("feature_note") or {}
    feats = [{"name": c, "weight": round(w.get(c, 0.0), 3), "note": note.get(c, "")} for c in (m.get("selected") or [])]
    # Bobot one-hot field diringkas jadi dua ujungnya saja — dua puluh baris di UI hanya
    # akan menenggelamkan sembilan fitur yang benar-benar perlu dibaca.
    oh = sorted(((c, w[c]) for c in cols if c not in (m.get("selected") or [])), key=lambda t: t[1])
    return {
        "trained": m.get("trained"),
        "variant": m.get("variant"),
        "auc": met.get("auc"),
        "gini": met.get("gini"),
        "n_rows": met.get("n_rows"),
        "n_errors": met.get("n_errors"),
        "features": feats,
        "n_field_cols": len(oh),
        "field_low": [[c, round(v, 2)] for c, v in oh[:3]],
        "field_high": [[c, round(v, 2)] for c, v in oh[-3:]],
    }


if __name__ == "__main__":
    m = load()
    if m is None:
        sys.exit(f"belum ada model di {MODEL_PATH} — jalankan scripts/conf_fit.py")
    print(json.dumps(info(), indent=2, ensure_ascii=False))
    for arg in sys.argv[1:]:
        print(f"\n{arg}")
        for f, s in sorted(score_file(arg).items(), key=lambda kv: kv[1]):
            print(f"   {s:>6.2f}  {f}")
