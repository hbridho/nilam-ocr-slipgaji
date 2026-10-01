"""Mesin OCR lokal (RapidOCR ONNX) — hanya untuk laptop tanpa akses ke layanan OCR.

Bukan jalur bawaan: menyalakannya menyeret rapidocr-onnxruntime beserta modelnya (ratusan MB) ke
dalam image, dan versi modelnya lalu dikelola per pod alih-alih di satu tempat. Pasang dengan
`pip install slip-ml[ocr]` dan `EXTRACTION_BACKEND=rapidocr`.
"""

from ocr_common.errors import BadRequest
from ocr_common.types import OcrEngineResult

from slip_ml.ocr import BACKEND_LOCAL, UnreadableDocument
from slip_ml.ocr import OcrEngine as _Engine


class LocalOcrEngine:
    name = "rapidocr"

    def __init__(self, *, max_pages: int = 20, render_dpi: int | None = None):
        self._engine = _Engine(BACKEND_LOCAL, max_pages=max_pages, render_dpi=render_dpi)

    def warmup(self) -> None:
        self._engine.warmup()

    def read(self, filename: str, content: bytes) -> OcrEngineResult:
        try:
            result = self._engine.read(content, filename)
        except UnreadableDocument as exc:
            raise BadRequest(str(exc)) from exc
        return {
            "pages": result["pages"],
            "full_text": result["full_text"],
            "model": result.get("model"),
            "engine": self.name,
            "elapsed_ms": result["elapsed_ms"],
            "n_pages": len(result["pages"]),
        }  # ty: ignore[invalid-return-type]
