"""Tahap structuring: teks OCR per halaman masuk, slip gaji terstruktur keluar.

Satu halaman = satu slip. Banyak berkas di korpus ini memuat tiga bulan sekaligus, dan
menggabungkan teksnya akan mencampur komponen satu bulan ke total bulan lain — itulah
sebabnya keluarannya daftar slip, bukan satu objek.

Lapisan LLM (arbitrase nilai uang lewat Bedrock) opsional: `use_llm=False` menjalankan
lapisan aturan saja. Tanpa LLM akurasinya ~81,5%, dengan arbitrase ~89,9%.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import Any

from slip_ml.fields import ALL_FIELDS, MONEY_FIELDS, blank, missing_mandatory
from slip_ml.vendor import ensure_path

ensure_path()

import s2_structure as _structure  # noqa: E402
from core.fields import as_int as _as_int  # noqa: E402

DEFAULT_LLM_MODEL = _structure.LLM_MODEL


class NoText(ValueError):
    """Tidak ada satu pun halaman dengan teks — tidak ada yang bisa distrukturkan."""


def structure_pages(
    pages: Iterable[Mapping[str, Any]],
    *,
    use_llm: bool = False,
    model: str | None = None,
    llm_all: bool | None = None,
) -> dict[str, Any]:
    """{slips, n_slips, llm_used, elapsed_ms} dari halaman hasil tahap OCR.

    `llm_all` sengaja default None, bukan False: None membiarkan config.yaml (`llm.all_fields`)
    yang memutuskan, sedangkan False MENIMPA-nya. Pernah salah di pipeline penelitian, dan
    arbitrase mati diam-diam tanpa jejak apa pun di keluaran.
    """
    started = time.perf_counter()
    pages = [page for page in pages]
    texts = [(page.get("text") or "") for page in pages]
    if not any(text.strip() for text in texts):
        raise NoText("tidak ada teks OCR pada dokumen ini")

    slips: list[dict[str, Any]] = []
    llm_used = False
    for index, page in enumerate(pages, start=1):
        text = page.get("text") or ""
        if not text.strip():
            continue
        fields, source, info = _structure.extract_fields(
            text, use_llm, model or DEFAULT_LLM_MODEL, page=page.get("page") or index, of=len(pages), llm_all=llm_all
        )
        checks = _structure.validate(fields, source)
        llm_used = llm_used or bool(info.get("llm_used"))
        slips.append(
            {
                "slip_no": len(slips) + 1,
                "page": page.get("page") or index,
                "fields": _normalise(fields),
                "source": {name: dict(entry) for name, entry in (source or {}).items()},
                "checks": {name: list(values) for name, values in (checks or {}).items()},
                "counts": _counts(fields, source, text),
                "llm": {
                    "used": bool(info.get("llm_used")),
                    "asked": bool(info.get("llm_asked")),
                    "error": info.get("llm_error"),
                    "compare": info.get("llm_compare") or {},
                    "disagree": list(info.get("llm_disagree") or ()),
                },
                "ocr_text": text,
                "ocr_confidence": page.get("confidence") or {},
                "missing_mandatory_fields": missing_mandatory(fields),
            }
        )
    if not slips:
        raise NoText("tidak ada halaman berisi teks")
    return {
        "slips": slips,
        "n_slips": len(slips),
        "llm_used": llm_used,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _normalise(fields: Mapping[str, Any]) -> dict[str, Any]:
    """20 kunci, selalu lengkap; nominal uang jadi integer rupiah.

    Nilai uang keluar dari pipeline sebagai string terformat ("2.400.000"), sedangkan kontrak
    API menjanjikan angka. Dinormalkan di sini, satu kali, supaya tahap scoring dan orchestrator
    tidak masing-masing menebak formatnya. Skor keyakinan tidak berubah karenanya: featuriser
    membaca nilai lewat str() dan as_int(), dan keduanya memberi hasil sama untuk "2400000" dan
    2400000.
    """
    out: dict[str, Any] = {}
    for name in ALL_FIELDS:
        value = fields.get(name)
        if blank(value):
            out[name] = None
        elif name in MONEY_FIELDS:
            number = _as_int(value)
            out[name] = number if number is not None else value
        else:
            out[name] = value
    return out


def _counts(fields: Mapping[str, Any], source: Mapping[str, Any], text: str) -> dict[str, int]:
    filled = sum(1 for value in fields.values() if not blank(value))
    by_regex = sum(1 for entry in source.values() if entry.get("how") == "regex")
    # Dihitung terpisah dengan sengaja: gaji bersih yang diturunkan tidak diextraction siapa pun,
    # dan memasukkannya ke by_llm akan mengkreditkan model atas sebuah pengurangan.
    by_derived = sum(1 for entry in source.values() if entry.get("how") == "derived")
    return {
        "filled": filled,
        "by_regex": by_regex,
        "by_derived": by_derived,
        "by_llm": filled - by_regex - by_derived,
        "chars": len(text),
    }
