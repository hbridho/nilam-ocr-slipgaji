#!/usr/bin/env python3
"""Bangun tabel latih untuk skor keyakinan: satu baris per NILAI yang dikeluarkan pipeline.

    python scripts/conf_build.py            # -> attempts/<attempt>/conf/rows_<stamp>.csv

Populasinya sengaja LEBIH LUAS daripada tabel evaluasi. Tabel itu hanya menghitung field
yang benar-benar ada di dokumen DAN nilainya terbaca di teks OCR — saringan yang butuh
ground truth, dan ground truth tidak ada saat serving. Jadi yang dikumpulkan di sini adalah
setiap nilai yang pipeline keluarkan, termasuk nilai untuk field yang sebenarnya tidak ada
di dokumen. Justru baris itu yang paling perlu diberi skor rendah: dari 204 baris semacam
itu, hanya 14,7% yang benar.

Label `y` memakai fields.same_value() — pembanding yang sama persis dengan tabel evaluasi,
sehingga skor dan tabel tidak mungkin berbeda pendapat soal apa itu "benar".
"""

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ocr_main"))

from core.fields import ALL_FIELDS, MONEY_FIELDS, MONEY_TOKEN, _loose, as_int, blank, same_value  # noqa: E402

# Kata yang tidak pernah jadi bagian sebuah NILAI. Kalau muncul di dalam nilai, yang terambil
# hampir pasti potongan label kolom sebelahnya — bukan jawabannya.
LABEL_WORD = re.compile(r"\b(total|jumlah|amount|rp|idr|nama|periode|no|tgl|tanggal)\b", re.I)

# Urutan kolom fitur. Dipakai bersama oleh conf_fit.py dan conf_score.py — satu tempat saja,
# supaya sisi latih dan sisi saji tidak pernah bergeser satu kolom tanpa ketahuan.
FEATURES = [
    # kesepakatan & asal
    "agree_yes",
    "agree_no",
    "agree_none",
    "how_regex",
    "how_llm",
    "how_derived",
    "in_text",
    "page_disagree",
    # bentuk nilai
    "shape_num",
    "shape_alpha",
    "shape_mixed",
    "value_len",
    "n_words",
    "is_zero",
    "has_label_word",
    # kualitas OCR
    "ocr_mean",
    "n_low_ratio",
    "lm_local",
    "has_line",
    "line_position",
    # konteks baris & aritmetika
    # `netpay_ok` sengaja TIDAK ada di sini. Cek gaji bersih yang "ok" ternyata 78,8% benar,
    # sementara yang "skipped" 86,5% — jadi menyandikannya sebagai sinyal positif itu
    # terbalik, dan membuangnya menaikkan AUC dari 0,8301 ke 0,8454. Yang tersisa,
    # `netpay_mismatch`, membawa bagian yang memang informatif: pecahnya identitas.
    "n_money_tokens",
    "earnings_ok",
    "earnings_mismatch",
    "netpay_mismatch",
    "component_gt_total",
    "is_money",
    "llm_ratio_page",
    # besaran nominal — kelompok terkuat kedua setelah `agree`
    "mag_outlier",
    "n_digits",
    "is_max_on_page",
    "label_len",
    "earnings_gap",
    "identity_residual",
    # apakah field ini pantas ADA sama sekali
    "exist_prior",
    "value_repeat",
]

# Yang BENAR-BENAR dilatih, dari 35 kolom di atas. Sisanya tetap dihitung dan ikut ditulis
# ke CSV supaya bisa diuji lagi kapan saja, tapi tidak masuk model.
#
# Dipilih dengan menguji langsung terhadap KEMURNIAN band teratas, bukan terhadap Gini —
# keduanya terbukti tidak sejalan. Himpunan 12-kolom ber-Gini 0,708 tidak punya band murni
# sama sekali, sementara himpunan ini ber-Gini 0,682 punya 288 nilai (19,6% populasi) yang
# nol error, lebih besar daripada model 35-fitur yang ber-Gini 0,721 (260 nilai). Delapan
# fitur lain yang sempat dicoba justru MENGHAPUS band murni jadi nol saat ditambahkan.
SELECTED = [
    "agree_yes",
    "agree_no",
    "agree_none",
    "mag_outlier",
    "exist_prior",
    "n_digits",
    "earnings_ok",
    "is_zero",
    "n_low_ratio",
]

# Apa yang diukur tiap fitur, untuk ditampilkan di halaman labeling.
FEATURE_NOTE = {
    "agree_yes": "regex dan LLM sepakat",
    "agree_no": "regex dan LLM berbeda",
    "agree_none": "tidak ada pembanding (satu sisi kosong)",
    "mag_outlier": "seberapa jauh nominalnya dari median field ini",
    "exist_prior": "seberapa sering field ini benar-benar ada di dokumen",
    "n_digits": "jumlah digit pada nilainya",
    "earnings_ok": "komponen menjumlah pas ke totalnya",
    "is_zero": "nilainya nol",
    "n_low_ratio": "porsi kotak OCR berkeyakinan rendah di halaman ini",
}


def fill_fitted(feats, field, value, line, fitted):
    """Isi tiga kolom yang bergantung pada parameter TERLATIH.

    lm_local, mag_outlier dan exist_prior tidak bisa diambil apa adanya dari CSV: ketiganya
    butuh tabel bigram, median per field, dan prior keberadaan — dan ketiganya harus datang
    dari fold latih saja. Satu fungsi dipakai sisi latih dan sisi saji supaya tidak mungkin
    berbeda.
    """
    out = dict(feats)
    lm = (fitted or {}).get("lm")
    s = lm_score(lm, line) if (lm and line) else None
    # -3 kira-kira nilai tengah pada korpus ini; baris yang tidak ada diberi nilai netral,
    # dan kolom has_line yang memberi tahu model bahwa itu bukan pengukuran.
    out["lm_local"] = (s if s is not None else -3.0) / 10.0
    out["mag_outlier"] = mag_outlier(field, value, (fitted or {}).get("medians"))
    # 0,8 untuk field yang tidak pernah muncul di fold latih: 0 akan membuatnya terlihat
    # seperti field yang terbukti jarang ada, padahal yang benar adalah "tidak tahu".
    out["exist_prior"] = ((fitted or {}).get("priors") or {}).get(field, 0.8)
    return out


# "components 9,321,500 vs total 17,059,426" -> dua angka itu. Cek aritmetika sudah
# menghitungnya; yang belum dipakai adalah SEBERAPA JAUH selisihnya, dan itu bergradasi
# sementara ok/mismatch hanya biner.
GAP_RE = re.compile(r"([\d.,]+)\s*vs\s*[a-z ]*([\d.,]+)", re.I)


def _num(s):
    try:
        return float(str(s).replace(".", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


# ── model bigram karakter, untuk lm_local ───────────────────────────────────
# Digit dilipat jadi '9': sebuah slip penuh angka yang semuanya berbeda, dan tanpa pelipatan
# ini setiap nominal terlihat "aneh" bagi modelnya. Yang ingin diukur adalah kekacauan OCR,
# bukan nominal mana yang jarang.


def _norm(s):
    return re.sub(r"\d", "9", (s or "").lower())


def fit_bigram(texts):
    """{'bi': Counter, 'uni': Counter, 'v': int} dari sekumpulan teks halaman."""
    bi, uni = Counter(), Counter()
    for t in texts:
        s = _norm(t)
        for i in range(len(s) - 1):
            uni[s[i]] += 1
            bi[s[i : i + 2]] += 1
    return {"bi": bi, "uni": uni, "v": len(uni) + 1}


def lm_score(model, s):
    """Rata-rata log-peluang bigram. Makin rendah, makin kacau barisnya.

    Dibaca dengan .get(), bukan []: saat melatih tabelnya Counter (yang mengembalikan 0 untuk
    kunci baru), tapi saat serving tabel yang sama datang dari JSON sebagai dict biasa. Bigram
    yang belum pernah terlihat — satu aksara Han hasil OCR yang meleset sudah cukup — akan
    melempar KeyError dan menjatuhkan seluruh permintaan, padahal nilai yang benar untuk kunci
    yang tak dikenal memang nol (smoothing +1 di bawah yang menanganinya).
    """
    s = _norm(s)
    if len(s) < 2:
        return None
    bi, uni, v = model["bi"], model["uni"], model["v"]
    tot = sum(math.log((bi.get(s[i : i + 2], 0) + 1) / (uni.get(s[i], 0) + v)) for i in range(len(s) - 1))
    return tot / (len(s) - 1)


# ── satu baris ──────────────────────────────────────────────────────────────


def page_context(text, lines, llm, oc, checks, counts, filled, vals):
    """Bagian fitur yang berlaku se-halaman — dihitung sekali, bukan per field.

    Satu tempat saja, dipakai bersama conf_build dan conf_score, karena sisi latih dan sisi
    saji yang menghitung konteks halaman secara terpisah adalah cara paling mudah membuat
    keduanya diam-diam berbeda.
    """
    tp = as_int(vals.get("total_pendapatan"))
    tk = as_int(vals.get("total_potongan"))
    gb = as_int(vals.get("gaji_bersih"))
    # Total Pendapatan - Total Potongan = Gaji Bersih. Kalau identitas ini pecah, salah satu
    # dari ketiganya salah — dan besar pecahnya lebih informatif daripada sekadar "pecah".
    identity = 0.0
    if tp is not None and tk is not None and gb is not None and tp:
        identity = min(abs((tp - tk) - gb) / max(1, abs(tp)), 1.0)

    gap = 0.0
    m = GAP_RE.search(" ".join(checks.get("earnings") or []))
    if m:
        x, y2 = _num(m.group(1)), _num(m.group(2))
        if x is not None and y2 is not None and max(x, y2) > 0:
            gap = min(abs(x - y2) / max(x, y2), 1.0)

    money = [as_int(v) for f, v in vals.items() if f in MONEY_FIELDS and not blank(v)]
    money = [x for x in money if x is not None]

    return {
        "loose_text": _loose(text),
        "n_lines": len(lines),
        "n_disagree": len(llm.get("disagree") or []),
        "ocr_mean": float(oc.get("mean") or 0.95),
        "n_low_ratio": (oc.get("n_low") or 0) / max(1, oc.get("n_boxes") or 1),
        "earnings": (checks.get("earnings") or ["?"])[0],
        "netpay": (checks.get("netpay") or ["?"])[0],
        "llm_ratio": (counts.get("by_llm") or 0) / max(1, filled),
        "total_pendapatan": tp,
        "max_money": max(money) if money else None,
        "earnings_gap": gap,
        "identity_residual": identity,
    }


def mag_outlier(field, value, medians):
    """|log10(nilai / median field ini)|, dibatasi 1. 0 = persis khas, 1 = jauh menyimpang.

    Fitur tunggal terkuat setelah `agree`: di bawah 0,15 akurasinya 96,4%, di atas 0,8
    hanya 34,3%. Median-nya parameter yang dipelajari, jadi harus datang dari data latih.
    """
    if field not in MONEY_FIELDS:
        return 0.0
    iv = as_int(value)
    med = (medians or {}).get(field)
    if not iv or not med:
        return 0.0
    return min(abs(math.log10(max(1, iv) / max(1, med))), 1.0)


def exist_priors(rows):
    """{field: fraksi baris latih di mana field itu memang ADA di dokumen}.

    Parameter yang dipelajari dari LABEL, jadi wajib dihitung di dalam fold — menghitungnya
    sekali dari seluruh korpus berarti label fold uji ikut membentuknya.
    """
    n, d = {}, {}
    for r in rows:
        f = r.get("field")
        if not f:
            continue
        d[f] = d.get(f, 0) + 1
        try:
            n[f] = n.get(f, 0) + int(r.get("exists") or 0)
        except (TypeError, ValueError):
            pass
    return {f: n.get(f, 0) / d[f] for f in d if d[f]}


def field_medians(rows):
    """{field: median nominal} dari baris latih. Dipakai mag_outlier."""
    buckets = {}
    for r in rows:
        f = r.get("field") if isinstance(r, dict) else None
        iv = r.get("_ivalue") if isinstance(r, dict) else None
        try:
            iv = int(iv) if iv not in (None, "", "None") else None
        except (TypeError, ValueError):
            iv = None
        if f and iv:
            buckets.setdefault(f, []).append(iv)
    return {f: sorted(v)[len(v) // 2] for f, v in buckets.items() if v}


def row_features(field, value, src, page, lm=None):
    """Fitur untuk satu nilai. `page` memuat yang berlaku se-halaman.

    `lm` dipisah dari sini dan diisi belakangan oleh conf_fit: model bigramnya harus
    dilatih DI DALAM fold, dan menghitungnya di sini akan memakai teks halaman uji.
    """
    v = str(value)
    line = src.get("line") or ""
    digits = sum(c.isdigit() for c in v)
    letters = sum(c.isalpha() for c in v)
    agree = src.get("_agree")
    how = src.get("how") or "none"
    iv = as_int(value) if field in MONEY_FIELDS else None
    tp = page.get("total_pendapatan")

    return {
        "agree_yes": int(agree is True),
        "agree_no": int(agree is False),
        "agree_none": int(agree is None),
        "how_regex": int(how == "regex"),
        "how_llm": int(how == "llm"),
        "how_derived": int(how == "derived"),
        "in_text": int(_loose(v) in page["loose_text"]),
        "page_disagree": min(page["n_disagree"], 4),
        "shape_num": int(digits > 0 and letters == 0),
        "shape_alpha": int(letters > 0 and digits == 0),
        "shape_mixed": int(digits > 0 and letters > 0),
        # dibagi 40 dan dibatasi 1: regresi logistik peka skala, dan satu kolom yang
        # rentangnya ratusan akan menenggelamkan kolom 0/1 di sebelahnya.
        "value_len": min(len(v) / 40.0, 1.0),
        "n_words": min(len(v.split()), 5) / 5.0,
        "is_zero": int(v.strip() in ("0", "0.0", "0,0")),
        "has_label_word": int(bool(LABEL_WORD.search(v))),
        "ocr_mean": page["ocr_mean"],
        "n_low_ratio": page["n_low_ratio"],
        "lm_local": 0.0,  # diisi conf_fit, di dalam fold
        "has_line": int(bool(line)),
        # 0 = atas halaman, 1 = bawah. Nilai dari LLM tidak punya baris; 0,5 menaruhnya di
        # tengah, yang netral, dan `has_line` yang memberi tahu model bahwa itu bukan
        # posisi sungguhan.
        "line_position": (src.get("line_no") or 0) / page["n_lines"] if line and page["n_lines"] else 0.5,
        "n_money_tokens": min(len(MONEY_TOKEN.findall(line)) if line else 0, 4) / 4.0,
        "earnings_ok": int(page["earnings"] == "ok"),
        "earnings_mismatch": int(page["earnings"] == "mismatch"),
        "netpay_mismatch": int(page["netpay"] == "mismatch"),
        # Sebuah komponen tidak bisa lebih besar dari totalnya. Jarang (8 baris), tapi ketika
        # terjadi hanya 37,5% yang benar — mustahil dalam aritmetika, dan murah untuk dicek.
        "component_gt_total": int(
            iv is not None
            and tp is not None
            and iv > tp
            and field not in ("total_pendapatan", "gaji_bersih", "total_potongan")
        ),
        "is_money": int(field in MONEY_FIELDS),
        "llm_ratio_page": page["llm_ratio"],
        # Diisi conf_fit/conf_score: butuh median per field dari data latih, dan median itu
        # harus dihitung DI DALAM fold seperti bigram — kalau tidak, nominal halaman uji
        # ikut membentuk acuannya.
        "mag_outlier": 0.0,
        # Untuk SEMUA field, bukan hanya uang: sebuah NIK berdigit 4 ("2183") sama tidak
        # masuk akalnya dengan gaji pokok berdigit 5. Terukur 5 digit 26,1% benar, 4 digit
        # 47,1%, 1 digit 57,3% — sementara 8 digit 92,0%. Dibagi 10 agar berskala 0-1.
        "n_digits": min(digits, 10) / 10.0,
        # Total pendapatan memang biasanya angka terbesar di halaman; komponen tidak.
        "is_max_on_page": int(iv is not None and page.get("max_money") is not None and iv == page["max_money"]),
        # Label pendek seperti "total b" jauh lebih mudah salah tangkap daripada
        # "sub total potongan": 1-4 huruf 78,5% benar, >=10 huruf 88,7%.
        "label_len": min(len(src.get("label") or "") / 20.0, 1.0),
        "earnings_gap": page.get("earnings_gap", 0.0),
        "identity_residual": page.get("identity_residual", 0.0),
        # Diisi conf_fit/conf_score dari data latih, seperti mag_outlier: seberapa sering
        # field ini benar-benar ADA di dokumen. Field yang jarang ada (<0,3) hanya 65,3%
        # benar ketika pipeline tetap mengeluarkan nilai untuknya, lawan 88,9% untuk field
        # yang hampir selalu ada. Ini yang menangkap kelas error terbesar di bin teratas:
        # labelnya bilang field tidak ada, tapi regex DAN llm sama-sama membaca sesuatu.
        "exist_prior": 0.0,
        # Nilai yang muncul berkali-kali di halaman lebih mudah tertukar; yang tidak muncul
        # sama sekali biasanya hasil normalisasi uang, dan itu justru benar.
        "value_repeat": min(page["loose_text"].count(_loose(v)) if _loose(v) else 0, 4) / 4.0,
    }


def _labeling():
    """Impor halaman labeling hanya ketika benar-benar mengumpulkan data latih.

    Featuriser di berkas ini juga dipakai sisi saji (service/libs/slip_ml me-vendor berkas
    ini apa adanya, supaya latih dan saji tidak mungkin bergeser). Sisi saji tidak punya
    berkas label, atribusi, maupun Flask — dan mengimpor labeling di tingkat modul menyeret
    seluruh halaman web itu ke dalam service.
    """
    global AN
    import labeling as AN  # noqa: PLW0603

    return AN


def collect(store, groups, backend, out_dir):
    """Kumpulkan seluruh baris. Mengembalikan (rows, texts) — texts untuk melatih bigram."""
    _labeling()
    out_root = AN.OCR_OUT / backend
    rows, texts = [], []
    n_docs = n_pages = 0

    for name, e in sorted(store.labels.items()):
        g = (groups.get(name) or {}).get("group") or ""
        if g not in AN.GROUPS or not AN.in_pile(name):
            continue
        if e.get("is_true_document") != "yes" or e.get("is_no_guardrail") != "yes":
            continue
        n_docs += 1
        stem = Path(name).stem
        n = e.get("n_pages") or AN.page_count(name)
        machine = AN.all_ocr(stem, n)
        srcs = AN.all_sources(stem, n)
        page_texts = AN.all_texts(stem, n)

        for page_no, pg in (e.get("pages") or {}).items():
            if pg.get("has_slip") is False or not (pg.get("slips") or []):
                continue
            text = page_texts.get(page_no, "")
            if not text:
                continue
            detail = out_root / f"{stem}_p{page_no}.detail.json"
            if not detail.is_file():
                detail = out_root / f"{stem}.detail.json"
            if not detail.is_file():
                continue
            d = json.loads(detail.read_text(encoding="utf-8"))
            n_pages += 1
            texts.append(text)

            llm = d.get("llm") or {}
            compare = llm.get("compare") or {}
            oc = d.get("ocr_confidence") or {}
            oc = oc if isinstance(oc, dict) else {}
            checks = d.get("checks") or {}
            counts = d.get("counts") or {}
            filled = counts.get("filled") or 0

            vals = ((machine.get(backend) or {}).get(int(page_no)) if str(page_no).isdigit() else None) or {}
            src_all = ((srcs.get(backend) or {}).get(str(page_no)) or {}).get("source") or {}
            lines = [ln for ln in text.splitlines() if ln.strip()]

            page = page_context(text, lines, llm, oc, checks, counts, filled, vals)

            for slip in pg.get("slips") or []:
                stages = AN.slip_stages(slip, vals, src_all, backend, text)
                for field in ALL_FIELDS:
                    value = vals.get(field)
                    if blank(value):
                        continue  # tidak ada nilai untuk dinilai
                    st = stages.get(field) or {}
                    src = dict(src_all.get(field) or {})
                    src["_agree"] = (compare.get(field) or {}).get("agree")
                    gt_all = slip.get("fields") or {}
                    gt = gt_all.get(field)
                    not_exist = set(slip.get("not_exist") or [])
                    feats = row_features(field, value, src, page)
                    # Target multiclass: field mana yang GT-nya sama dengan nilai ini. Satu
                    # nilai bisa cocok ke beberapa field (gaji_bersih == total_pendapatan
                    # kalau tanpa potongan); field yang ditugaskan didahulukan, supaya
                    # "benar" di sini tetap berarti persis sama dengan y di bawah.
                    matches = [
                        g
                        for g in ALL_FIELDS
                        if g not in not_exist and not blank(gt_all.get(g)) and same_value(g, gt_all.get(g), value)
                    ]
                    target = field if field in matches else (matches[0] if matches else "none")
                    rows.append(
                        {
                            **feats,
                            "field": field,
                            "y": int(same_value(field, gt, value)),
                            # untuk GroupKFold: satu dokumen berisi beberapa slip nyaris kembar,
                            # dan memisahkannya secara acak akan melebihkan hasil validasi
                            "doc": name,
                            "page": page_no,
                            "group": g,
                            # dua kolom outcome untuk tabel binning, bukan fitur
                            "ocr_true": int(st.get("ocr") == AN.TRUE),
                            "exists": int(bool(st.get("exists"))),
                            # dihitung ulang di dalam fold: lm_local dari _line, mag_outlier
                            # dari _ivalue plus median field milik fold itu
                            "_line": src.get("line") or "",
                            "_ivalue": as_int(value) if field in MONEY_FIELDS else "",
                            # untuk model multiclass (conf_mc_fit.py) — tidak ditulis ke CSV V1
                            "_value": str(value),
                            "_how": src.get("how") or "none",
                            "_target": target,
                            "_ambiguous": int(len(matches) > 1),
                            "_gt_blank": int(blank(gt) or field in not_exist),
                            "_page_vals": {k: v for k, v in vals.items() if not blank(v)},
                        }
                    )

    print(f"  {n_docs} dokumen · {n_pages} halaman · {len(rows)} nilai")
    return rows, texts


def main():
    _labeling()
    ap = argparse.ArgumentParser(description="bangun tabel latih skor keyakinan")
    ap.add_argument("attempt", nargs="?", help="attempt yang dipakai (default: yang terbaru)")
    ap.add_argument("--backend", default=AN.DEFAULT_BACKEND)
    ap.add_argument("--out", help="tulis ke berkas ini alih-alih folder attempt")
    args = ap.parse_args()

    a = AN.choose_attempt(args.attempt)
    AN.ATTEMPT = a["attempt"]
    groups = AN.load_groups(a["csv"])
    store = AN.LabelStore(AN.LABEL_DIR)

    out_dir = AN.ATTEMPTS_DIR / a["attempt"] / "conf"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, texts = collect(store, groups, args.backend, out_dir)
    if not rows:
        sys.exit("tidak ada baris — apakah reference/ root terisi dan OCR sudah dijalankan?")

    ok = sum(r["y"] for r in rows)
    print(f"  benar {ok} ({ok / len(rows) * 100:.1f}%) · salah {len(rows) - ok}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path(args.out) if args.out else out_dir / f"rows_{stamp}.csv"
    cols = FEATURES + ["field", "y", "doc", "page", "group", "ocr_true", "exists", "_line", "_ivalue"]
    with dest.open("w", newline="", encoding="utf-8") as f:
        # extrasaction="ignore": baris juga membawa kolom `_...` milik model multiclass yang
        # hanya dipakai di memori; CSV V1 sengaja tetap berkolom sama seperti sebelumnya.
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # Teks halaman disimpan terpisah: conf_fit melatih bigramnya sendiri per fold, dan
    # menaruh teks penuh di CSV fitur akan membuatnya berlipat ganda.
    (dest.with_suffix(".texts.json")).write_text(json.dumps({"texts": texts}, ensure_ascii=False), encoding="utf-8")
    print(f"\n  {dest}")
    print(f"  {dest.with_suffix('.texts.json')}  ({len(texts)} halaman, untuk bigram)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
