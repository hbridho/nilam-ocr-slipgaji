from collections.abc import Sequence

from ocr_common.errors import BadRequest
from ocr_common.types import OcrPage, StructuringResult

from app.ml.base import Structurer


class StructuringService:
    """Membuang halaman kosong, menjalankan structurer, dan membentuk hasil tahap ini."""

    def __init__(self, structurer: Structurer):
        self._structurer = structurer

    def structure(self, pages: Sequence[OcrPage]) -> StructuringResult:
        cleaned = [page for page in pages if (page.get("text") or "").strip()]
        if not cleaned:
            raise BadRequest("tidak ada teks OCR yang bisa distrukturkan")
        return self._structurer.structure(cleaned)
