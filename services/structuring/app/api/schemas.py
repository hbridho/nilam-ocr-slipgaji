from typing import Any, Literal

from pydantic import BaseModel, Field

from ocr_common.pipeline.schemas import GuardrailsPayload
from ocr_common.slip_gaji import SLIP_FIELDS
from ocr_common.web.schemas import JobStatusBase, SuccessEnvelope

FIELD_LIST = ", ".join(f"`{name}`" for name in SLIP_FIELDS)


class OcrPageIn(BaseModel):
    page: int = Field(1, ge=1, description="Nomor halaman, mulai dari 1", examples=[1])
    text: str = Field(
        ..., description="Teks OCR halaman ini, urut baca", examples=["SLIP GAJI\nGaji Pokok  Rp 4.500.000"]
    )
    confidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Ringkasan skor OCR halaman ini; diteruskan ke tahap scoring sebagai fitur mutu OCR",
    )


class StructureRequest(BaseModel):
    pages: list[OcrPageIn] = Field(
        ...,
        min_length=1,
        description=(
            "Halaman hasil OCR. **Satu halaman = satu slip**: berkas tiga bulan menghasilkan tiga slip. "
            "Jangan menggabungkan teks halaman — komponen satu bulan akan tercampur ke total bulan lain"
        ),
    )


class SlipOut(BaseModel):
    slip_no: int = Field(..., ge=1, description="Nomor urut slip dalam dokumen ini", examples=[1])
    page: int = Field(..., ge=1, description="Halaman asal slip ini", examples=[1])
    fields: dict[str, Any] = Field(
        ...,
        description=(
            f"20 field, selalu lengkap; null bila tidak ditemukan. Nominal uang sudah integer. Field: " f"{FIELD_LIST}"
        ),
    )
    source: dict[str, Any] = Field(
        default_factory=dict,
        description="Asal tiap nilai: `how` (regex | llm | derived), sinonim label yang cocok, dan baris OCR-nya",
    )
    checks: dict[str, Any] = Field(default_factory=dict, description="Hasil cek aritmetika slip")
    counts: dict[str, Any] = Field(default_factory=dict, description="Berapa field terisi, dan dari lapisan mana")
    llm: dict[str, Any] = Field(
        default_factory=dict, description="Jejak arbitrase LLM; `compare` dibaca model keyakinan"
    )
    missing_mandatory_fields: list[str] = Field(
        default_factory=list, description="Field wajib yang tidak terisi pada slip ini"
    )


class StructuringResult(BaseModel):
    document_type: str = Field(..., examples=["slip_gaji"])
    n_slips: int = Field(..., ge=0, description="Jumlah slip yang ditemukan di dokumen ini", examples=[3])
    slips: list[SlipOut] = Field(..., description="Satu entri per slip, urut halaman")
    llm_used: bool = Field(False, description="true bila arbitrase LLM benar-benar dipanggil")
    elapsed_ms: float | None = Field(None, description="Waktu structuring, milidetik", examples=[227.5])
    reject_reason: str | None = Field(
        None, description="Terisi bila aturan structuring menolak dokumen; pipeline berhenti dan klien menerima 400"
    )


class StructureResponse(SuccessEnvelope):
    data: StructuringResult


class StructuringJobRequest(BaseModel):
    request_id: str = Field(
        ..., description="request_id dari orchestrator", examples=["OCR_9cb01af2-493d-446d-b191-af120333f6d0"]
    )
    document_type: str = Field("slip_gaji", examples=["slip_gaji"])
    guardrails: GuardrailsPayload | None = Field(
        None, description="Laporan guardrail dari tahap OCR; diteruskan apa adanya sampai hasil akhir"
    )
    ocr: dict[str, Any] | None = Field(
        None,
        description=(
            "Hasil tahap OCR (`pages[]`, `full_text`). Boleh dihilangkan bila `PIPELINE_HANDOFF_BY_REFERENCE` "
            "menyala: service ini membacanya dari `ocr_results` memakai request_id"
        ),
    )
    pipeline_name_sequence: list[str] | None = Field(
        None,
        description="Urutan service permintaan ini; null = pipeline penuh. Bila `structuring` terakhir, job ini "
        "mengakhiri permintaan dan tidak diteruskan ke scoring",
        examples=[None],
    )
    column_confidence_threshold: dict[str, float] | None = Field(
        None, description="Ambang per field (0-1) dari permintaan; diteruskan ke scoring", examples=[None]
    )


class StructuringJobStatus(JobStatusBase):
    stage: Literal["STRUCTURING"] = Field("STRUCTURING", examples=["STRUCTURING"])
    result: StructuringResult | None = Field(None, description="Hasil structuring setelah `status` menjadi `DONE`")


class StructuringJobStatusResponse(SuccessEnvelope):
    data: StructuringJobStatus
