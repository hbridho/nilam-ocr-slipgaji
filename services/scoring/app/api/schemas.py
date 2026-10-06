import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ocr_common.pipeline.schemas import GuardrailsPayload, StructuringPayload
from ocr_common.thresholds import InvalidThreshold, parse_column_thresholds
from ocr_common.web.schemas import JobStatusBase, SuccessEnvelope


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
        examples=[{"gaji_bersih": 0.9}],
    )


class ScoringDirectRequest(BaseModel):
    request_id: str | None = Field(None, description="Diulang di respons; opsional", examples=["QC_1"])
    document_type: str = Field("slip_gaji", description="Hanya `slip_gaji`", examples=["slip_gaji"])
    guardrails: GuardrailsPayload | None = Field(
        None, description="Laporan guardrail; opsional, dikembalikan apa adanya"
    )
    structuring: StructuringPayload = Field(
        ...,
        description=(
            "Hasil tahap structuring apa adanya (`data` dari `POST /v1/structuring-direct`). `source`, `checks`, "
            "`counts`, `llm.compare`, `ocr_text` dan `ocr_confidence` tiap slip harus ikut: fitur terkuat model "
            "berasal dari jejak itu"
        ),
    )
    column_confidence_threshold: dict[str, float] | None = Field(
        None,
        description='Ambang per field (0-1), `{"all_field": 0.8}` atau `{"gaji_bersih": 0.9}`; kunci field menang',
        examples=[{"all_field": 0.8}],
    )

    @field_validator("column_confidence_threshold", mode="before")
    @classmethod
    def _columns(cls, value: Any) -> dict[str, float] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("column_confidence_threshold must be a JSON object")
        try:
            return parse_column_thresholds(json.dumps(value))
        except InvalidThreshold as exc:
            raise ValueError(str(exc)) from exc


class ScoringDirectResult(ScoringResult):
    data: dict[str, Any] = Field(
        ...,
        description=(
            "Kontrak `extract-ocr`: `{total_slip, slip[]}`, tiap field `{value, confidence 0/1}` menurut "
            "`column_confidence_threshold`, lalu `FIELD_CONFIDENCE_THRESHOLD`"
        ),
    )


class ScoringDirectResponse(SuccessEnvelope):
    data: ScoringDirectResult


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
            "ini membacanya dari `nilam_structuring_results` memakai request_id"
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
