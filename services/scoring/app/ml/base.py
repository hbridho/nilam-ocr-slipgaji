from collections.abc import Sequence
from typing import Any, Protocol


class ConfidenceModel(Protocol):
    """Model keyakinan per field: P(nilai ini benar | fitur).

    Masukannya satu slip lengkap dengan jejaknya (nilai, asal tiap nilai, cek aritmetika,
    teks dan skor OCR halaman), bukan sekadar daftar nilai: fitur terkuat model ini justru
    berasal dari jejak itu — kesepakatan regex vs LLM, jarak nominal ke median field, dan porsi
    kotak OCR berskor rendah di halaman.
    """

    name: str
    metadata: dict[str, Any]

    def score(self, slips: Sequence[dict[str, Any]]) -> list[dict[str, Any]]: ...
