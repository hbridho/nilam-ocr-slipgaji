"""Fitur STRUKTUR untuk guardrail — melengkapi TF-IDF.

TF-IDF hanya tahu kata apa yang muncul. Yang tidak ia tahu adalah apakah kata-kata itu
tersusun seperti slip gaji: label field yang dikenali pipeline, nilai yang benar-benar bisa
dibaca regex di sebelahnya, dan komponen yang menjumlah ke totalnya. Surat keterangan
penghasilan memuat "gaji pokok" juga — tapi jarang punya daftar komponen yang menjumlah pas.

Deterministik, tanpa LLM, tanpa sklearn: dipakai bersama sisi latih dan saji.
"""

import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ocr_main"))

import s2_structure as S  # noqa: E402
from core.fields import ALL_FIELDS, EARNINGS, MANDATORY, MONEY_TOKEN, as_int, blank, keep_content_lines  # noqa: E402

LONG_DIGITS = re.compile(r"\d{12,}")  # NIK, nomor rekening, nomor surat
RP = re.compile(r"\b(rp|idr)\b", re.I)
PERIOD_WORD = re.compile(r"\b(periode|bulan|month|period)\b", re.I)
LETTER = re.compile(r"\b(surat|keterangan|menerangkan|yang bertanda tangan|dengan ini|demikian)\b", re.I)
BANK = re.compile(r"\b(saldo|mutasi|rekening koran|debet|kredit|transaksi)\b", re.I)

STRUCT_NAMES = [
    "n_label_fields",
    "n_label_mandatory",
    "n_regex_fields",
    "n_regex_mandatory",
    "regex_gaji_pokok",
    "regex_total",
    "earnings_sum_ok",
    "net_identity_ok",
    "money_lines",
    "rp_count",
    "long_digit_count",
    "period_word",
    "letter_words",
    "bank_words",
    "log_chars",
    "log_lines",
    "digit_ratio",
]


BOX_GAP = re.compile(r"\s{2,}")  # s1_ocr memisahkan kotak sebaris dengan 2 spasi
MONEY_END = re.compile(r"\d[\d.,]{2,}\s*(,-)?\s*$")
KEY_VALUE = re.compile(r"^[A-Za-z][\w .()/]{1,30}\s*[:：]\s*\S")

TEXT_LAYOUT_NAMES = [
    "boxes_per_line",
    "two_col_lines",
    "three_col_lines",
    "money_at_end",
    "key_value_lines",
    "caps_lines",
    "short_lines",
    "label_then_money",
]


def text_layout_features(text: str):
    """Tata letak yang masih tersisa di teks OCR: kotak per baris, kolom, perataan nilai.

    s1_ocr menyusun ulang baris kiri-ke-kanan dan menaruh DUA spasi di antara kotak yang
    sebaris, jadi "Gaji Pokok  Rp 2.400.000" adalah dua kotak di satu baris — label di kiri,
    nominal di kanan. Pola itulah bentuk slip gaji; surat keterangan berisi paragraf.
    """
    lines = keep_content_lines(text or "")
    if not lines:
        return [0.0] * len(TEXT_LAYOUT_NAMES)
    n = len(lines)
    boxes = [len([b for b in BOX_GAP.split(ln) if b.strip()]) for ln in lines]
    lab_money = 0
    for ln in lines:
        parts = [b for b in BOX_GAP.split(ln) if b.strip()]
        if len(parts) >= 2 and re.search(r"[A-Za-z]{3,}", parts[0]) and MONEY_END.search(ln):
            lab_money += 1
    return [
        min(sum(boxes) / n / 4.0, 1.0),
        sum(b >= 2 for b in boxes) / n,
        sum(b >= 3 for b in boxes) / n,
        sum(bool(MONEY_END.search(ln)) for ln in lines) / n,
        sum(bool(KEY_VALUE.match(ln)) for ln in lines) / n,
        sum(ln.isupper() for ln in lines) / n,
        sum(len(ln.split()) <= 4 for ln in lines) / n,
        lab_money / n,
    ]


def struct_features(text: str):
    lines = keep_content_lines(text or "")
    labels = set()
    for ln in lines:
        try:
            labels |= {f for f, _, _ in S.all_labels(ln)}
        except Exception:
            pass
    try:
        rec, _, _ = S.match_fields(text or "")
    except Exception:
        rec = {}
    got = {f for f in ALL_FIELDS if not blank(rec.get(f))}

    comps = [as_int(rec.get(f)) for f in EARNINGS if not blank(rec.get(f))]
    comps = [c for c in comps if c]
    gross = as_int(rec.get("total_pendapatan")) if "total_pendapatan" in got else None
    ded = as_int(rec.get("total_potongan")) if "total_potongan" in got else None
    net = as_int(rec.get("gaji_bersih")) if "gaji_bersih" in got else None

    n_chars = len(text or "")
    digits = sum(c.isdigit() for c in (text or ""))
    money_lines = sum(1 for ln in lines if MONEY_TOKEN.search(ln))
    return [
        len(labels) / len(ALL_FIELDS),
        len(labels & set(MANDATORY)) / len(MANDATORY),
        len(got) / len(ALL_FIELDS),
        len(got & set(MANDATORY)) / len(MANDATORY),
        float("gaji_pokok" in got),
        float("total_pendapatan" in got or "gaji_bersih" in got),
        float(bool(gross) and len(comps) >= 2 and sum(comps) == gross),
        float(bool(gross) and ded is not None and net is not None and gross - ded == net),
        min(money_lines / max(1, len(lines)), 1.0),
        math.log1p(len(RP.findall(text or ""))),
        math.log1p(len(LONG_DIGITS.findall(text or ""))),
        float(bool(PERIOD_WORD.search(text or ""))),
        math.log1p(len(LETTER.findall(text or ""))),
        math.log1p(len(BANK.findall(text or ""))),
        math.log1p(n_chars),
        math.log1p(len(lines)),
        digits / max(1, n_chars),
    ]
