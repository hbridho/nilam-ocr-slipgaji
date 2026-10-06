from collections.abc import Mapping, Sequence
from typing import Any

from ocr_common.errors import BadRequest
from ocr_common.types import OcrPage, StructuringResult

from app.ml.base import Structurer


class StructuringService:
    """Membuang halaman kosong, menjalankan structurer, dan membentuk hasil tahap ini."""

    def __init__(self, structurer: Structurer):
        self._structurer = structurer

    @staticmethod
    def pages_from_ocr(ocr: Mapping[str, Any]) -> list[OcrPage]:
        """Hasil tahap OCR (`pages`) sebagai halaman yang dibaca structurer, nilai bawaan diisi.
        Satu halaman = satu slip: halaman TIDAK digabung."""
        return [
            {
                "page": page.get("page") or index,
                "text": page.get("text") or "",
                "confidence": page.get("confidence") or {},
            }
            for index, page in enumerate(ocr.get("pages") or [], start=1)
        ]

    def structure(self, pages: Sequence[OcrPage]) -> StructuringResult:
        cleaned = [page for page in pages if (page.get("text") or "").strip()]
        if not cleaned:
            raise BadRequest("tidak ada teks OCR yang bisa distrukturkan")
        return self._structurer.structure(cleaned)
