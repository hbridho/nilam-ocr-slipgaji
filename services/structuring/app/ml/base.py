from collections.abc import Sequence
from typing import Protocol

from ocr_common.types import OcrPage, StructuringResult


class Structurer(Protocol):
    """Mengubah teks OCR per halaman menjadi slip gaji terstruktur.

    Satu halaman = satu slip: berkas yang memuat tiga bulan menghasilkan tiga slip, masing-masing
    dengan 20 fieldnya sendiri. Keluarannya `StructuringResult` (`ocr_common.types`), termasuk
    `source`, `checks` dan `counts` per slip — bukan hiasan, melainkan fitur yang dibaca model
    keyakinan di tahap scoring.
    """

    name: str

    def structure(self, pages: Sequence[OcrPage]) -> StructuringResult: ...
