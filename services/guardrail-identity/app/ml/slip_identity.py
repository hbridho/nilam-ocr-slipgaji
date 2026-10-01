"""Model identitas yang sebenarnya, dari `slip_ml.guard`.

numpy saja saat serving: tidak ada sklearn dan tidak ada pickle di image, dan tidak ada OpenCV —
yang dibaca teks OCR, bukan piksel halaman.
"""

from typing import Any

from app.ml.base import IdentityCheck
from slip_ml import guard


class SlipIdentityCheck:
    name = "slip_identity"

    def __init__(self) -> None:
        info = guard.model_info(guard.KIND_TEXT)
        if info is None:
            raise RuntimeError("model identitas tidak ada di image; jalankan service/scripts/sync_ml.py --apply")
        self.reject_threshold = float(info["threshold"])
        self.metadata: dict[str, Any] = dict(info)

    def check(self, text: str, threshold: float) -> dict[str, Any]:
        return guard.check_identity(text, reject_threshold=threshold)


def build_identity_check() -> IdentityCheck:
    return SlipIdentityCheck()
