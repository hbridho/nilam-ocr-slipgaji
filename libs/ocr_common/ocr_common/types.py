"""Bentuk data yang berpindah antar tahap, sebagai TypedDict.

Ini kembaran Python dari payload Pydantic di `ocr_common.pipeline.schemas`: schema memvalidasi
apa yang datang lewat HTTP, TypedDict di sini menjelaskan apa yang dioper engine, service dan
pipeline di dalam memori. Saat runtime semuanya dict biasa, jadi tidak ada yang berubah pada
apa yang disimpan atau dikirim.
"""

from typing import Any, NotRequired, TypedDict


class BoundingBox(TypedDict):
    """Kotak tegak di sekeliling satu baris teks, dalam piksel gambar yang dibaca model OCR."""

    x1: float
    y1: float
    x2: float
    y2: float


class OcrBlock(TypedDict):
    """Satu baris teks hasil pengenalan."""

    text: str
    confidence: float
    bbox: NotRequired[BoundingBox | None]
    page: NotRequired[int]


class OcrPage(TypedDict):
    """Satu halaman hasil OCR. Halaman TIDAK digabung: satu berkas slip gaji lazim memuat tiga
    bulan, dan menggabungkan teksnya mencampur komponen satu bulan ke total bulan lain."""

    page: int
    text: str
    confidence: dict[str, Any]


class OcrEngineResult(TypedDict):
    """Keluaran engine OCR (`app/ml/*` milik extraction)."""

    pages: list[OcrPage]
    full_text: str
    model: str | None


class OcrResult(OcrEngineResult):
    """Hasil tersimpan tahap OCR (`ocr_results.result`), diteruskan ke structuring dan scoring."""

    engine: str
    elapsed_ms: float
    n_pages: int
    guardrails: NotRequired[dict[str, Any] | None]


class StructuredSlip(TypedDict):
    """Satu slip gaji (satu halaman) yang sudah terstruktur.

    `source`, `checks`, `counts`, `llm`, `ocr_text` dan `ocr_confidence` bukan hiasan: model
    keyakinan membacanya sebagai fitur (kesepakatan regex-LLM, aritmetika halaman, mutu OCR),
    jadi tahap scoring butuh semuanya apa adanya.
    """

    slip_no: int
    page: int
    fields: dict[str, Any]
    source: dict[str, dict[str, Any]]
    checks: dict[str, list[str]]
    counts: dict[str, int]
    llm: dict[str, Any]
    ocr_text: str
    ocr_confidence: dict[str, Any]
    missing_mandatory_fields: list[str]


class StructuringResult(TypedDict):
    """Hasil tersimpan tahap structuring: daftar slip beserta jenis dokumennya."""

    document_type: str
    slips: list[StructuredSlip]
    n_slips: int
    llm_used: bool
    elapsed_ms: NotRequired[float]
    reject_reason: NotRequired[str | None]


class SlipScores(TypedDict):
    """Skor keyakinan satu slip: {field: P(benar), 0-1}. Field tanpa nilai tidak diberi skor — tidak ada
    yang perlu dinilai di sana, dan memberinya 0 akan tercampur dengan nilai yang diragukan."""

    slip_no: int
    scores: dict[str, float]


class ScoringResult(TypedDict):
    """Hasil tersimpan tahap scoring."""

    slips: list[SlipScores]
    threshold: float
    model: NotRequired[str | None]


class FinalSlip(TypedDict):
    """Satu slip pada hasil akhir: nilai, skor, dan field wajib yang tidak terisi."""

    slip_no: int
    page: int
    fields: dict[str, Any]
    scores: dict[str, float]
    missing_mandatory_fields: list[str]


class FinalResult(TypedDict):
    """Apa yang dihasilkan pipeline untuk satu permintaan: dibawa callback SCORING dan dipakai
    menyusun `data` kontrak `extract-ocr`."""

    document_type: str
    total_slip: int
    slips: list[FinalSlip]
    guardrails: dict[str, Any] | None
    llm_used: NotRequired[bool]


class ContractField(TypedDict):
    """Satu field di kontrak `extract-ocr`: nilainya dan bendera keyakinan 0/1."""

    value: Any
    confidence: int


class ContractSlip(TypedDict):
    """Satu entri `data.slip[]`: 20 field ber-`{value, confidence}`, halamannya, dan daftar
    field wajib yang tidak terisi. Kuncinya ditulis dinamis dari `slip_gaji.SLIP_FIELDS`, jadi
    hanya kunci tetapnya yang disebut di sini."""

    page: int | None
    missing_mandatory_fields: list[str]


class ContractData(TypedDict):
    """`data` kontrak `extract-ocr`: jumlah slip dan daftarnya."""

    total_slip: int
    slip: list[ContractSlip]
