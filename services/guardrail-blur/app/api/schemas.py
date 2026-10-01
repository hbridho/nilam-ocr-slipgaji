from typing import Any, Literal

from pydantic import BaseModel, Field

from ocr_common.web.schemas import SuccessEnvelope

RID = "OCR_9cb01af2-493d-446d-b191-af120333f6d0"


class TextCheckRequest(BaseModel):
    request_id: str | None = Field(None, description="Diulang di respons; opsional", examples=[RID])
    text: str = Field(
        ...,
        description="Teks OCR seluruh halaman dokumen, digabung dengan baris baru. Boleh kosong",
        examples=["SLIP GAJI\nOKTOBER 2023\nNama  : RATNA SUSANTI\nGaji Pokok  Rp 2.400.000"],
    )
    confidence: dict[str, Any] | None = Field(
        None,
        description=(
            "Ringkasan skor OCR halaman: `mean`, `min`, `n_boxes`, `n_low` (kotak berskor < 0,90). "
            "**Inilah masukan utama pemeriksaan ini.** Tanpa ringkasan ini gerbang hanya melihat "
            "panjang teks — masih bekerja, tetapi jauh lebih tumpul, karena dokumen buram kerap "
            "menghasilkan banyak karakter yang semuanya salah"
        ),
        examples=[{"n_boxes": 84, "mean": 0.9812, "min": 0.4123, "n_low": 4}],
    )


class BlurReport(BaseModel):
    check: Literal["blur"] = Field("blur", description="Pemeriksaan yang menjawab")
    verdict: Literal["blur", "ok", "mutu_tak_terukur"] = Field(
        ...,
        description=(
            "`blur` halaman terlalu rusak untuk diekstraksi · `ok` masih terbaca · "
            "`mutu_tak_terukur` ringkasan skor OCR tidak dikirim, jadi tidak ada yang bisa diukur. "
            "Yang terakhir juga menahan dokumen, tetapi ia kekurangan pada **permintaan**, bukan "
            "temuan tentang dokumennya — diperbaiki dengan mengirim `confidence`, bukan dengan "
            "memfoto ulang"
        ),
        examples=["ok"],
    )
    passed: bool = Field(..., description="true ketika halaman masih cukup terbaca", examples=[True])
    reason: str | None = Field(
        None, description="Alasan berbahasa Indonesia yang bisa ditampilkan apa adanya; null bila lolos"
    )
    p_broken: float | None = Field(
        None, ge=0, le=1, description="P(halaman ini terlalu rusak untuk diekstraksi)", examples=[0.0153]
    )
    threshold: float | None = Field(
        None, description="Ambang yang dipakai: `p_broken >= threshold` berarti buram", examples=[0.9584]
    )
    chars: int = Field(..., ge=0, description="Jumlah karakter teks OCR setelah dipangkas", examples=[1789])
    ocr_mean: float | None = Field(None, description="Skor OCR rata-rata yang dikirim", examples=[0.9812])
    blank: bool = Field(
        ...,
        description=(
            "Halaman ini juga kosong menurut batas gerbang mutu. **Tidak memutuskan apa pun di "
            "sini**: pada halaman tanpa teks model ini memang berkata buram, dan yang berhak "
            "mendahulukan `blank` adalah orchestrator saat menggabungkan ketiga guardrail"
        ),
        examples=[False],
    )
    model: str = Field(..., description="Backend yang menjawab", examples=["slip_blur"])


class BlurReportResponse(SuccessEnvelope):
    message: str = Field("OK", description="Ringkasan hasil", examples=["OK"])
    data: BlurReport
