"""Mesin OCR tiruan untuk pengembangan lokal dan tes: tidak memuat model apa pun.

Teksnya slip gaji yang sah dan bisa distrukturkan, supaya seluruh rantai (guardrail ->
structuring -> scoring) bisa dijalankan di laptop tanpa mesin OCR. Hanya boleh dengan
ENVIRONMENT=local: config.py menolaknya di luar itu, karena hasil tiruan tetap hasil karangan.
"""

from ocr_common.types import OcrEngineResult

PAGE_TEMPLATE = """PT SUMBER REJEKI MAKMUR
SLIP GAJI
Periode  : {periode}
Nama  : ANDI SAPUTRA
NIK  : 202309001
Jabatan  : Staff Gudang
PENGHASILAN
Gaji Pokok  Rp 4.500.000
Tunjangan Makan  Rp 600.000
TOTAL PENDAPATAN  Rp 5.100.000
POTONGAN
TOTAL POTONGAN  Rp 225.000
GAJI BERSIH  Rp 4.875.000
"""
PERIODS = ("Februari 2025", "Maret 2025", "April 2025")


class MockOcrEngine:
    name = "mock"

    def __init__(self, n_pages: int = 1):
        self._n_pages = n_pages

    def read(self, filename: str, content: bytes) -> OcrEngineResult:
        # Nama berkas yang memuat "3slip" memberi tiga halaman: bentuk yang paling sering
        # diunggah adalah tiga bulan dalam satu berkas, dan tes butuh cara memintanya.
        n_pages = 3 if "3slip" in filename else self._n_pages
        pages = [
            # Ringkasan mutu seperti layanan OCR: tanpa n_boxes/min, gerbang blur yang asli melihat
            # halaman yang hancur.
            {
                "page": i,
                "text": PAGE_TEMPLATE.format(periode=PERIODS[(i - 1) % 3]),
                "confidence": {"engine": "mock", "mean": 0.99, "min": 0.93, "p10": 0.97, "n_boxes": 24, "n_low": 0},
            }
            for i in range(1, n_pages + 1)
        ]
        return {
            "pages": pages,
            "full_text": "\n".join(page["text"] for page in pages),
            "model": "mock",
            "engine": "mock",
            "elapsed_ms": 1.0,
            "n_pages": n_pages,
        }  # ty: ignore[invalid-return-type]
