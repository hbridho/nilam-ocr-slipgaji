from typing import Any

from ocr_common.errors import BadRequest

from app.ml.base import ConfidenceModel


class ConfidenceService:
    """Memberi skor keyakinan pada setiap slip hasil structuring."""

    def __init__(self, model: ConfidenceModel, threshold: float):
        self._model = model
        self._threshold = threshold

    def score(self, structuring: dict[str, Any]) -> dict[str, Any]:
        slips = structuring.get("slips") or []
        if not slips:
            raise BadRequest("tidak ada slip untuk dinilai")
        return {
            "slips": self._model.score(slips),
            "threshold": self._threshold,
            "model": getattr(self._model, "name", None),
        }
