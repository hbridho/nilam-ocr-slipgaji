"""Model keyakinan tiruan untuk tes: skor tetap, tanpa memuat model.

Hanya dengan ENVIRONMENT=local. Skornya sengaja dua tingkat (0,95 untuk nilai uang, 0,40 untuk
sisanya) supaya tes bisa membedakan confidence 1 dari 0 pada ambang bawaan 0,5 tanpa model asli.
"""

from collections.abc import Sequence
from typing import Any

HIGH, LOW = 0.95, 0.40
MONEY_HINT = ("gaji", "tunjangan", "total", "bonus", "insentif", "lembur", "thr")


class MockConfidenceModel:
    name = "mock"
    metadata: dict[str, Any] = {"variant": "mock"}

    def score(self, slips: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for slip in slips:
            scores = {
                name: (HIGH if any(hint in name for hint in MONEY_HINT) else LOW)
                for name, value in (slip.get("fields") or {}).items()
                if value is not None and str(value).strip()
            }
            out.append({"slip_no": slip.get("slip_no"), "scores": scores})
        return out
