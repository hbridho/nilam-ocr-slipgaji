"""Gerbang mutu tiruan: memutuskan dari skor OCR rata-rata saja, tanpa memuat bobot.

Hanya boleh dengan ENVIRONMENT=local — config.py menolaknya di luar itu, karena guardrail tiruan
berarti tidak ada penjaga sama sekali. Putusannya deterministik dan dijelaskan: `mean` di bawah
0,70 jadi buram, selain itu lolos. Tanpa `confidence` ia selalu meloloskan, sama seperti model asli
yang tanpa ringkasan skor hanya bisa melihat panjang teks.
"""

from typing import Any

BLUR_MEAN = 0.70
THRESHOLD = 0.9
REASON = "Dokumen terlalu buram untuk dibaca. Mohon foto ulang dengan lebih jelas."
BLANK_MAX_CHARS = 20


class MockBlurCheck:
    name = "mock"
    threshold = THRESHOLD
    metadata: dict[str, Any] = {"kind": "mock", "threshold": THRESHOLD}

    def check(self, text: str, confidence: dict[str, Any] | None, threshold: float | None) -> dict[str, Any]:
        limit = THRESHOLD if threshold is None else threshold
        chars = len((text or "").strip())
        mean = (confidence or {}).get("mean")
        # Sama seperti model asli: tanpa ringkasan skor OCR tidak ada mutu yang bisa diukur, dan
        # menyebut itu "buram" berarti menahan dokumen yang baik atas masukan yang tidak dikirim.
        if not (confidence or {}):
            return {
                "check": "blur",
                "verdict": "mutu_tak_terukur",
                "passed": False,
                "reason": (
                    "Mutu halaman tidak bisa diukur: ringkasan skor OCR (`mean`, `min`, `n_boxes`, "
                    "`n_low`) tidak dikirim. Ini kekurangan pada permintaan, bukan temuan tentang "
                    "dokumennya."
                ),
                "p_broken": None,
                "threshold": round(limit, 4),
                "chars": chars,
                "ocr_mean": None,
                "blank": chars <= BLANK_MAX_CHARS,
            }
        p_broken = 0.99 if mean is not None and mean < BLUR_MEAN else 0.01
        blur = p_broken >= limit
        return {
            "check": "blur",
            "verdict": "blur" if blur else "ok",
            "passed": not blur,
            "reason": REASON if blur else None,
            "p_broken": p_broken,
            "threshold": round(limit, 4),
            "chars": chars,
            "ocr_mean": round(float(mean), 4) if mean is not None else None,
            "blank": chars <= BLANK_MAX_CHARS,
        }
