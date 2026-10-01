from typing import Any, Protocol


class IdentityCheck(Protocol):
    """Pemeriksaan 3 dari 3: berkas ini slip gaji, atau dokumen lain?

    TF-IDF unigram+bigram atas teks OCR ditambah 8 ciri tata letak baris, dengan kalibrasi Platt.
    AUC 0,911 out-of-fold; pada ambang yang dipakai 97,2% slip asli lolos dan 41 dari 51 dokumen
    bukan-slip tertahan.

    Modelnya berbasis TEKS, bukan piksel: diukur pada korpus yang sama, model piksel hanya mencapai
    AUC 0,638. Karena teks OCR baru ada setelah tahap OCR, guardrail slip gaji berjalan SETELAH OCR
    dan sebelum structuring — bukan sebelum pipeline seperti guardrail berbasis gambar.
    """

    name: str
    reject_threshold: float
    metadata: dict[str, Any]

    def check(self, text: str, threshold: float) -> dict[str, Any]: ...
