"""Model keyakinan per field slip gaji, lewat `slip_ml.confidence`.

Regresi logistik atas 9 variabel + one-hot field, dikalibrasi isotonic. Dilatih pada 1.428 nilai
dari 50 dokumen berlabel; out-of-fold (GroupKFold per dokumen): AUC 0,826 · Gini 0,652 · ECE 0,037.
Pada ambang 80 (bawaan pemetaan confidence 0/1 di kontrak API): precision 93,4%, recall 75,6%.

numpy saja saat serving — tidak ada sklearn dan tidak ada pickle di image.
"""

from collections.abc import Sequence
from typing import Any

from slip_ml.confidence import ConfidenceUnavailable, model_info, score_slip


class SlipConfidenceModel:
    name = "conf_v2"

    def __init__(self):
        info = model_info()
        if info is None:
            raise RuntimeError(
                "model keyakinan (conf.json) tidak ada di image; jalankan service/scripts/sync_ml.py --apply"
            )
        self.metadata: dict[str, Any] = dict(info)

    def score(self, slips: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for slip in slips:
            try:
                scores = score_slip(slip)
            except ConfidenceUnavailable as exc:  # model hilang setelah service berjalan
                raise RuntimeError(str(exc)) from exc
            out.append({"slip_no": slip.get("slip_no"), "scores": scores})
        return out
