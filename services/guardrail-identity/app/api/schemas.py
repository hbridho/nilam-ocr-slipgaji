from typing import Any, Literal

from pydantic import BaseModel, Field

from ocr_common.web.schemas import SuccessEnvelope

RID = "OCR_9cb01af2-493d-446d-b191-af120333f6d0"


class TextCheckRequest(BaseModel):
    request_id: str | None = Field(None, description="Diulang di respons; opsional", examples=[RID])
    text: str = Field(
        ...,
        description=(
            "Teks OCR seluruh halaman dokumen, digabung dengan baris baru. Baris pembungkus BRISPOT "
            "('Foto Slip Gaji', 'BRISPOT', 'Halaman x dari y') tidak perlu dibuang: model "
            "membuangnya sendiri, karena baris itu tercetak juga pada berkas yang bukan slip gaji"
        ),
        examples=["SLIP GAJI\nOKTOBER 2023\nNama  : RATNA SUSANTI\nGaji Pokok  Rp 2.400.000"],
    )
    confidence: dict[str, Any] | None = Field(
        None,
        description=(
            "Ringkasan skor OCR halaman. Diterima supaya ketiga guardrail bisa dipanggil dengan "
            "badan permintaan yang sama, tetapi **tidak dibaca di sini**: yang menilai mutu adalah "
            "`guardrail-blur`"
        ),
        examples=[{"n_boxes": 84, "mean": 0.9812, "min": 0.4123, "n_low": 4}],
    )


class IdentityReport(BaseModel):
    check: Literal["identity"] = Field("identity", description="Pemeriksaan yang menjawab")
    verdict: Literal["slip_gaji", "bukan_slip_gaji"] = Field(
        ..., description="`slip_gaji` lanjut · `bukan_slip_gaji` unggah dokumen yang benar", examples=["slip_gaji"]
    )
    passed: bool = Field(..., description="true hanya untuk `slip_gaji`", examples=[True])
    reason: str | None = Field(
        None, description="Alasan berbahasa Indonesia yang bisa ditampilkan apa adanya; null bila lolos"
    )
    proba_slip_gaji: float | None = Field(None, ge=0, le=1, description="P(berkas ini slip gaji)", examples=[0.9979])
    confidence: float | None = Field(
        None, ge=0, le=1, description="Keyakinan pada putusan ini, bukan pada kelas `slip_gaji`"
    )
    reject_threshold: float | None = Field(
        None, description="Lolos ketika `proba_slip_gaji >= reject_threshold`", examples=[0.47]
    )
    model: str = Field(..., description="Backend yang menjawab", examples=["slip_identity"])


class IdentityReportResponse(SuccessEnvelope):
    message: str = Field("OK", description="Ringkasan hasil", examples=["OK"])
    data: IdentityReport
