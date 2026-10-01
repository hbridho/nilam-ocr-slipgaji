"""Model identitas tiruan: menghitung kata khas slip gaji, tanpa memuat bobot.

Hanya boleh dengan ENVIRONMENT=local — config.py menolaknya di luar itu, karena guardrail tiruan
berarti tidak ada penjaga sama sekali. Putusannya deterministik dan dijelaskan: setiap kata khas
menambah 0,2 pada peluang, dan dokumen lolos ketika peluangnya mencapai ambang.
"""

import re
from typing import Any

SLIP_WORDS = re.compile(r"\b(gaji|slip|penghasilan|tunjangan|potongan|pendapatan|lembur|thr)\b", re.I)
REASON = "Dokumen ini bukan slip gaji. Mohon unggah slip gaji."


class MockIdentityCheck:
    name = "mock"
    reject_threshold = 0.5
    metadata: dict[str, Any] = {"kind": "mock"}

    def check(self, text: str, threshold: float) -> dict[str, Any]:
        hits = len(SLIP_WORDS.findall(text or ""))
        probability = min(0.05 + 0.2 * hits, 0.99)
        passed = probability >= threshold
        return {
            "check": "identity",
            "verdict": "slip_gaji" if passed else "bukan_slip_gaji",
            "passed": passed,
            "reason": None if passed else REASON,
            "proba_slip_gaji": round(probability, 4),
            "confidence": round(probability if passed else 1 - probability, 4),
            "reject_threshold": round(threshold, 4),
            "model": "mock",
        }
