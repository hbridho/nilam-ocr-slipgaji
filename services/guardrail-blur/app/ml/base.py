from typing import Any, Protocol


class BlurCheck(Protocol):
    """Pemeriksaan 2 dari 3: halaman ini masih cukup terbaca untuk diekstraksi?

    Regresi logistik atas 6 ciri mutu OCR — skor rata-rata, skor minimum, porsi kotak berskor
    rendah, jumlah kotak, jumlah karakter, karakter per kotak. AUC 0,985.

    Tidak ada satu kata pun yang dibaca, dan itu bukan kelalaian: gerbang ini tidak boleh peduli
    dokumennya berbunyi apa, supaya ia tetap bekerja pada dokumen yang belum pernah dilihatnya.
    Menambahkan TF-IDF kata ke gerbang ini hanya menaikkan AUC 0,989 -> 0,992, tidak sebanding
    dengan kehilangan sifat itu.
    """

    name: str
    threshold: float
    metadata: dict[str, Any]

    def check(self, text: str, confidence: dict[str, Any] | None, threshold: float | None) -> dict[str, Any]: ...
