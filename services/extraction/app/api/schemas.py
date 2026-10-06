from typing import Any, Literal

from pydantic import BaseModel, Field

from ocr_common.web.schemas import JobStatusBase, SuccessEnvelope


class OcrPage(BaseModel):
    page: int = Field(..., ge=1, description="Nomor halaman, mulai dari 1", examples=[1])
    text: str = Field(
        ...,
        description="Teks halaman ini dalam urutan baca. Kotak yang sebaris dipisah dua spasi",
        examples=["SLIP GAJI\nPeriode  : Februari 2025\nGaji Pokok  Rp 4.500.000"],
    )
    confidence: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Ringkasan skor keyakinan mesin OCR untuk halaman ini: `mean`, `min`, `p10`, `n_boxes`, `n_low` "
            "(kotak berskor < 0,90). Tahap scoring memakainya sebagai fitur mutu OCR"
        ),
        examples=[{"engine": "rapidocr", "n_boxes": 84, "mean": 0.9812, "min": 0.4123, "p10": 0.9102, "n_low": 4}],
    )


class GuardrailDocument(BaseModel):
    verdict: Literal["accepted", "reject"] = Field(..., description="Putusan dokumen", examples=["accepted"])
    confidence: float | None = Field(
        None,
        ge=0,
        le=1,
        description=(
            "Lolos: P(slip gaji) dari guardrail-identity. Ditolak: keyakinan pada penolakan dari pemeriksaan yang "
            "menolak (blank selalu 1,0)"
        ),
        examples=[0.9934],
    )
    n_pages: int = Field(0, ge=0, description="Halaman yang dinilai", examples=[3])
    threshold: float | None = Field(None, description="Ambang yang dipakai pemeriksaan penentu", examples=[0.47])


class GuardrailReport(BaseModel):
    passed: bool = Field(..., description="false berarti dokumen ditahan dan pipeline berhenti di tahap ini")
    reason: str | None = Field(None, description="Alasan ditahan, berbahasa Indonesia; null bila lolos")
    verdict: str = Field(
        ...,
        description="`blank`, `blur`, `mutu_tak_terukur`, `bukan_slip_gaji`, `slip_gaji`, atau `skipped`",
        examples=["slip_gaji"],
    )
    rejected_by: Literal["blank", "blur", "identity"] | None = Field(
        None, description="Pemeriksaan yang menolak; null bila lolos"
    )
    document: GuardrailDocument
    checks: dict[str, Any] = Field(
        default_factory=dict,
        description="Laporan tiap service guardrail (`blank`, `blur`, `identity`); null untuk yang tidak menjawab",
    )
    skipped: list[str] = Field(
        default_factory=list, description="Guardrail yang dimatikan (`GUARDRAIL_<NAMA>_ENABLED=false`)"
    )
    unavailable: list[str] = Field(
        default_factory=list,
        description="Guardrail yang menyala tetapi tidak menjawab; dilewati karena `GUARDRAILS_FAIL_OPEN` menyala",
    )
    pages: list[dict[str, Any]] = Field(
        default_factory=list, description="Kosong: guardrail slip gaji menilai teks seluruh dokumen, bukan per halaman"
    )
    skipped_reason: str | None = Field(None, description="Kenapa tidak ada yang menilai; null bila dinilai")


class OcrResult(BaseModel):
    engine: str = Field(
        ..., description="Backend OCR yang membaca dokumen (`EXTRACTION_BACKEND`)", examples=["rapidocr"]
    )
    model: str | None = Field(None, description="Identitas model yang dilaporkan mesin OCR", examples=["rapidocr"])
    elapsed_ms: float = Field(..., description="Waktu di dalam mesin OCR, milidetik", examples=[2841.7])
    n_pages: int = Field(..., ge=0, description="Halaman yang dibaca", examples=[3])
    full_text: str = Field(
        ...,
        description=(
            "Seluruh halaman digabung dengan baris baru. Dipakai guardrail; tahap structuring memakai "
            "`pages[]` agar satu bulan tidak tercampur ke bulan lain"
        ),
    )
    pages: list[OcrPage] = Field(..., description="Satu entri per halaman, urut")
    guardrails: GuardrailReport | None = Field(
        None,
        description=(
            "Putusan ketiga guardrail atas teks di atas; null ketika `pipeline_name_sequence` tidak memuat "
            "`guardrails`"
        ),
    )
    reject_reason: str | None = Field(
        None,
        description=(
            "Terisi ketika guardrail menahan dokumen. Job ini tetap `DONE` dan hasil OCR-nya tersimpan, "
            "tetapi pipeline tidak diteruskan ke structuring dan orchestrator menjawab 400 dengan `guardrails: 1`"
        ),
    )


class ExtractResponse(SuccessEnvelope):
    data: OcrResult


class OcrJobStatus(JobStatusBase):
    stage: Literal["OCR"] = Field("OCR", description="Selalu `OCR` di service ini", examples=["OCR"])
    result: OcrResult | None = Field(None, description="Hasil OCR setelah `status` menjadi `DONE`; null sebelum itu")


class OcrJobStatusResponse(SuccessEnvelope):
    data: OcrJobStatus
