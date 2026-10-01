from typing import Protocol

from ocr_common.types import OcrEngineResult


class OcrEngine(Protocol):
    """Mesin OCR yang mengubah byte dokumen menjadi teks PER HALAMAN.

    Halaman sengaja tidak digabung. Satu berkas slip gaji lazim memuat tiga bulan berturut-turut,
    dan menggabungkan teksnya membuat komponen satu bulan tercampur ke total bulan lain — tahap
    structuring yang memperlakukan satu halaman sebagai satu slip.
    """

    name: str

    def read(self, filename: str, content: bytes) -> OcrEngineResult: ...
