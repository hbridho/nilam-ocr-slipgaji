"""Gerbang mutu yang sebenarnya, dari `slip_ml.guard`.

numpy saja saat serving: tidak ada sklearn dan tidak ada pickle di image, dan tidak ada OpenCV —
yang dibaca adalah ringkasan skor OCR, bukan piksel halaman.
"""

from typing import Any

from app.ml.base import BlurCheck
from slip_ml import guard


class SlipBlurCheck:
    name = "slip_blur"

    def __init__(self) -> None:
        info = guard.quality_info()
        if info is None:
            raise RuntimeError("model gerbang mutu tidak ada di image; jalankan service/scripts/sync_ml.py --apply")
        self.threshold = float(info["threshold"])
        self.metadata: dict[str, Any] = dict(info)

    def check(self, text: str, confidence: dict[str, Any] | None, threshold: float | None) -> dict[str, Any]:
        report = guard.check_blur(text, confidence)
        if threshold is None:
            return report
        # Ambang dari setelan menimpa ambang model; laporannya selalu mencatat ambang yang
        # benar-benar dipakai, bukan yang tersimpan.
        p_broken = report["p_broken"]
        blur = p_broken is not None and p_broken >= threshold
        return {
            **report,
            "verdict": guard.VERDICT_BLUR if blur else "ok",
            "passed": not blur,
            "reason": guard.REASONS[guard.VERDICT_BLUR] if blur else None,
            "threshold": round(threshold, 4),
        }


def build_blur_check() -> BlurCheck:
    return SlipBlurCheck()
