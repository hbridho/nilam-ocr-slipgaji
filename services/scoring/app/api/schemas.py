from typing import Any, Literal

from pydantic import BaseModel, Field

from ocr_common.pipeline.schemas import GuardrailsPayload, SlipPayload
from ocr_common.web.schemas import JobStatusBase, SuccessEnvelope


class ScoreRequest(BaseModel):
    slips: list[SlipPayload] = Field(
        ...,
        min_length=1,
        description=(
            "Slip hasil tahap structuring, apa adanya. `source`, `checks`, `counts`, `llm.compare`, `ocr_text` "
            "dan `ocr_confidence` harus ikut: fitur terkuat model justru berasal dari jejak itu — kesepakatan "
            "regex vs LLM, jarak nominal ke median field, dan porsi kotak OCR berskor rendah di halaman"
        ),
    )


class SlipScores(BaseModel):
    slip_no: int = Field(..., ge=1, examples=[1])
    scores: dict[str, float] = Field(
        ...,
        description="{field: P(nilai ini benar), 0-1}. Hanya field yang ada nilainya yang diberi skor",
        examples=[{"gaji_pokok": 0.936, "tunjangan_makan": 0.8889, "total_pendapatan": 0.9247}],
    )


class ScoringResult(BaseModel):
    slips: list[SlipScores] = Field(..., description="Satu entri per slip")
    threshold: float = Field(
        ...,
        ge=0,
        le=1,
        description=(
            "Ambang bawaan (0-1) yang memisahkan `confidence: 1` dari `0` di kontrak `extract-ocr` "
            "(`FIELD_CONFIDENCE_THRESHOLD`); `column_confidence_threshold` permintaan menimpanya per field. Skor "
            "mentah tetap dikembalikan, supaya ambangnya bisa diubah tanpa melatih ulang apa pun"
        ),
        examples=[0.5],
    )
    model: str | None = Field(None, description="Backend model yang memberi skor", examples=["conf_v2"])
    column_confidence_threshold: dict[str, float] | None = Field(
        None,
        description="Ambang per field dari permintaan (`column_confidence_threshold`); null bila tidak dikirim",
        examples=[{"gaji_bersih": 0.9, "all_field": 0.6}],
    )


class ScoreResponse(SuccessEnvelope):
    data: ScoringResult


class ScoringJobRequest(BaseModel):
    request_id: str = Field(..., examples=["OCR_9cb01af2-493d-446d-b191-af120333f6d0"])
    document_type: str = Field("slip_gaji", examples=["slip_gaji"])
    guardrails: GuardrailsPayload | None = Field(
        None, description="Laporan guardrail dari tahap OCR; dikembalikan apa adanya di hasil akhir"
    )
    ocr: dict[str, Any] | None = Field(None, description="Hasil tahap OCR; opsional")
    structuring: dict[str, Any] | None = Field(
        None,
        description=(
            "Hasil tahap structuring. Boleh dihilangkan bila `PIPELINE_HANDOFF_BY_REFERENCE` menyala: service "
            "ini membacanya dari `structuring_results` memakai request_id"
        ),
    )
    pipeline_name_sequence: list[str] | None = Field(
        None, description="Urutan service permintaan ini; null = pipeline penuh", examples=[None]
    )
    column_confidence_threshold: dict[str, float] | None = Field(
        None,
        description="Ambang per field (0-1) dari permintaan; disimpan bersama job supaya GET menjawab sama",
        examples=[{"gaji_bersih": 0.9}],
    )


class ScoringJobStatus(JobStatusBase):
    stage: Literal["SCORING"] = Field("SCORING", examples=["SCORING"])
    result: ScoringResult | None = Field(None, description="Skor keyakinan setelah `status` menjadi `DONE`")


class ScoringJobStatusResponse(SuccessEnvelope):
    data: ScoringJobStatus
