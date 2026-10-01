from typing import Any, Literal

from pydantic import BaseModel, Field

from ocr_common.web.schemas import SuccessEnvelope

RID = "OCR_9cb01af2-493d-446d-b191-af120333f6d0"


class TextCheckRequest(BaseModel):
    request_id: str | None = Field(None, description="Diulang di respons; opsional", examples=[RID])
    text: str = Field(
        ...,
        description=(
            "Teks OCR seluruh halaman dokumen, digabung dengan baris baru. Boleh kosong — itu "
            "bukan galat, melainkan jawaban `blank`"
        ),
        examples=["SLIP GAJI\nOKTOBER 2023\nNama  : RATNA SUSANTI\nGaji Pokok  Rp 2.400.000"],
    )
    confidence: dict[str, Any] | None = Field(
        None,
        description=(
            "Ringkasan skor OCR halaman. Diterima supaya ketiga guardrail bisa dipanggil dengan "
            "badan permintaan yang sama, tetapi **tidak dibaca di sini**: yang diputuskan "
            "pemeriksaan ini hanya panjang teks"
        ),
        examples=[{"n_boxes": 84, "mean": 0.9812, "min": 0.4123, "n_low": 4}],
    )


class BlankReport(BaseModel):
    check: Literal["blank"] = Field("blank", description="Pemeriksaan yang menjawab")
    verdict: Literal["blank", "ok"] = Field(
        ..., description="`blank` halaman tanpa teks · `ok` ada teks yang bisa dinilai", examples=["ok"]
    )
    passed: bool = Field(..., description="true ketika halaman TIDAK kosong", examples=[True])
    reason: str | None = Field(
        None, description="Alasan berbahasa Indonesia yang bisa ditampilkan apa adanya; null bila lolos"
    )
    chars: int = Field(..., ge=0, description="Jumlah karakter teks OCR setelah dipangkas", examples=[1789])
    max_chars: int = Field(
        ..., ge=0, description="Batas yang dipakai: `chars <= max_chars` berarti kosong", examples=[20]
    )
    model: str = Field(..., description="Backend yang menjawab", examples=["slip_blank"])


class BlankReportResponse(SuccessEnvelope):
    message: str = Field("OK", description="Ringkasan hasil", examples=["OK"])
    data: BlankReport
