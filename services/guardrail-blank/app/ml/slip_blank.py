"""Pemeriksaan kosong yang sebenarnya, dari `slip_ml.guard`.

Satu baris aturan, jadi tidak ada bobot yang dimuat dan tidak ada numpy yang dipanggil. Yang
diambil dari berkas gerbang mutu hanyalah `blank_max_chars`, supaya batasnya tetap satu angka yang
sama dengan yang dipakai saat melatih gerbang mutu — kalau batas itu berbeda di dua tempat,
halaman di perbatasan akan dinilai kosong oleh yang satu dan buram oleh yang lain.
"""

from typing import Any

from app.ml.base import BlankCheck
from slip_ml import guard


class SlipBlankCheck:
    name = "slip_blank"

    def __init__(self) -> None:
        quality = guard.quality_info() or {}
        self.metadata: dict[str, Any] = {
            "kind": "rule",
            "blank_max_chars": quality.get("blank_max_chars", 20),
        }

    def check(self, text: str, max_chars: int | None) -> dict[str, Any]:
        report = guard.check_blank(text)
        if max_chars is None:
            return report
        # Batas dari setelan menimpa batas model; laporannya selalu mencatat batas yang benar-benar
        # dipakai, bukan yang tersimpan.
        chars = report["chars"]
        blank = chars <= max_chars
        return {
            **report,
            "verdict": guard.VERDICT_BLANK if blank else "ok",
            "passed": not blank,
            "reason": guard.REASONS[guard.VERDICT_BLANK] if blank else None,
            "max_chars": max_chars,
        }


def build_blank_check() -> BlankCheck:
    return SlipBlankCheck()
