"""Pemeriksaan kosong tiruan: aturan yang sama, tanpa membaca berkas model.

Hanya boleh dengan ENVIRONMENT=local — config.py menolaknya di luar itu. Untuk pemeriksaan ini
bedanya dengan yang asli tipis (keduanya membandingkan panjang teks), tetapi `mock` tetap
dipisahkan supaya tes tidak bergantung pada ada-tidaknya berkas model di image.
"""

from typing import Any

BLANK_MAX_CHARS = 20
REASON = "Dokumen kosong: tidak ada teks yang terbaca. Pastikan halaman yang diunggah benar."


class MockBlankCheck:
    name = "mock"
    metadata: dict[str, Any] = {"kind": "mock", "blank_max_chars": BLANK_MAX_CHARS}

    def check(self, text: str, max_chars: int | None) -> dict[str, Any]:
        limit = BLANK_MAX_CHARS if max_chars is None else max_chars
        chars = len((text or "").strip())
        blank = chars <= limit
        return {
            "check": "blank",
            "verdict": "blank" if blank else "ok",
            "passed": not blank,
            "reason": REASON if blank else None,
            "chars": chars,
            "max_chars": limit,
        }
