"""Model Pydantic untuk payload yang dioper antar tahap dan ke orchestrator, dipakai route untuk
validasi dan oleh dokumen OpenAPI.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ocr_common.web.schemas import REQUEST_ID_EXAMPLE, Stage

Verdict = Literal["accepted", "reject", "skipped"]


class _Forwarded(BaseModel):
    model_config = ConfigDict(extra="allow")


class GuardrailsDocument(_Forwarded):
    """Putusan guardrail untuk dokumen ini (bentuk API spec [07])."""

    verdict: Verdict | None = Field(None, description="Putusan dokumen", examples=["accepted"])
    confidence: float | None = Field(
        None, ge=0, le=1, description="Lolos: P(slip gaji). Ditolak: keyakinan pada penolakan", examples=[0.9934]
    )
    n_pages: int | None = Field(None, ge=0, description="Halaman yang dinilai", examples=[3])
    threshold: float | None = Field(None, description="Ambang yang dipakai pemeriksaan penentu", examples=[0.47])
    threshold_target: Literal["accept", "reject"] | None = Field(None, description="Sisi ambang")


class GuardrailsPayload(_Forwarded):
    """Laporan guardrail, diteruskan apa adanya sampai hasil akhir."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": (
                "Laporan gabungan ketiga guardrail (guardrail-blank, guardrail-blur, guardrail-identity), "
                "dihasilkan tahap OCR karena modelnya membaca teks OCR, lalu diteruskan tanpa diubah ke "
                "structuring dan scoring."
            )
        }
    )

    passed: bool | None = Field(None, description="true: dokumen boleh lanjut ke structuring", examples=[True])
    reason: str | None = Field(None, description="Alasan ditahan; null bila lolos", examples=[None])
    verdict: str | None = Field(None, description="Putusan gabungan", examples=["slip_gaji"])
    skipped: list[str] | None = Field(
        None, description="Guardrail yang dimatikan (`GUARDRAIL_<NAMA>_ENABLED=false`)", examples=[[]]
    )
    unavailable: list[str] | None = Field(
        None, description="Guardrail yang menyala tetapi tidak menjawab (dilewati, fail-open)", examples=[[]]
    )
    skipped_reason: str | None = Field(None, description="Kenapa tidak ada putusan")
    document: GuardrailsDocument | None = None


# Nama lama, dipertahankan supaya kode yang sudah mengimpornya tidak putus.
GuardrailsResult = GuardrailsPayload


class OcrPagePayload(_Forwarded):
    """Satu halaman hasil OCR."""

    page: int = Field(1, ge=1, description="Nomor halaman, mulai dari 1", examples=[1])
    text: str = Field(..., description="Teks halaman ini dalam urutan baca")
    confidence: dict[str, Any] = Field(
        default_factory=dict, description="Ringkasan skor OCR halaman ini (mean, min, p10, n_low)"
    )


class OcrPayload(_Forwarded):
    """Hasil tahap OCR seperti diterima tahap berikutnya."""

    model_config = ConfigDict(
        json_schema_extra={"description": "Hasil tahap OCR (`ocr_results`), diteruskan ke structuring dan scoring."}
    )

    engine: str | None = Field(None, description="Backend OCR yang membaca dokumen", examples=["rapidocr"])
    model: str | None = Field(None, description="Identitas model yang dilaporkan mesin OCR", examples=["rapidocr"])
    elapsed_ms: float | None = Field(None, description="Waktu di dalam mesin OCR, milidetik", examples=[2841.7])
    n_pages: int | None = Field(None, ge=0, description="Halaman yang dibaca", examples=[3])
    full_text: str | None = Field(None, description="Seluruh halaman digabung; dipakai guardrail")
    pages: list[OcrPagePayload] = Field(
        default_factory=list,
        description=(
            "Satu entri per halaman. **Satu halaman = satu slip**: berkas yang memuat tiga bulan menghasilkan "
            "tiga slip, dan menggabungkan teksnya akan mencampur komponen satu bulan ke total bulan lain"
        ),
    )


class SlipPayload(_Forwarded):
    """Satu slip gaji hasil structuring."""

    slip_no: int = Field(..., ge=1, description="Nomor urut slip dalam dokumen", examples=[1])
    page: int | None = Field(None, ge=1, description="Halaman asal slip ini", examples=[1])
    fields: dict[str, Any] = Field(
        ..., description="20 field, selalu lengkap; null bila tidak ditemukan. Nominal uang sudah integer"
    )
    source: dict[str, Any] = Field(
        default_factory=dict,
        description="Asal tiap nilai (`how`: regex | llm | derived, sinonim label, baris OCR) — fitur model keyakinan",
    )
    checks: dict[str, Any] = Field(default_factory=dict, description="Hasil cek aritmetika slip")
    counts: dict[str, Any] = Field(default_factory=dict, description="Berapa field terisi, dan dari lapisan mana")
    llm: dict[str, Any] = Field(
        default_factory=dict, description="Jejak arbitrase LLM; `compare` dibaca model keyakinan"
    )
    ocr_text: str | None = Field(None, description="Teks halaman asal — fitur mutu OCR di tahap scoring")
    ocr_confidence: dict[str, Any] = Field(default_factory=dict, description="Skor OCR halaman asal")
    missing_mandatory_fields: list[str] = Field(
        default_factory=list, description="Field wajib yang tidak terisi pada slip ini"
    )


class StructuringPayload(_Forwarded):
    """Hasil tahap structuring seperti diterima tahap scoring."""

    model_config = ConfigDict(
        json_schema_extra={"description": "Hasil tahap structuring (`structuring_results`), diteruskan ke scoring."}
    )

    document_type: str | None = Field(None, description="Jenis dokumen", examples=["slip_gaji"])
    n_slips: int | None = Field(None, ge=0, description="Jumlah slip di dokumen ini", examples=[3])
    slips: list[SlipPayload] = Field(..., description="Satu entri per slip, urut halaman")
    llm_used: bool | None = Field(None, description="true bila arbitrase LLM benar-benar dipanggil")
    reject_reason: str | None = Field(None, description="Terisi bila aturan structuring menolak dokumen")


class SlipScoresPayload(_Forwarded):
    """Skor keyakinan satu slip."""

    slip_no: int = Field(..., ge=1, examples=[1])
    scores: dict[str, float] = Field(
        ...,
        description=(
            "{field: P(nilai ini benar), 0-1}, dikalibrasi isotonic. Hanya field yang ada nilainya yang " "diberi skor"
        ),
        examples=[{"gaji_pokok": 0.936, "total_pendapatan": 0.9247}],
    )


class ScoringPayload(_Forwarded):
    """Hasil tahap scoring."""

    slips: list[SlipScoresPayload] = Field(..., description="Satu entri per slip")
    threshold: float | None = Field(
        None, description="Ambang bawaan (0-1) yang memisahkan confidence 1 dari 0 di kontrak API", examples=[0.5]
    )


class FinalSlip(BaseModel):
    """Satu slip pada hasil akhir."""

    slip_no: int = Field(..., ge=1, examples=[1])
    page: int | None = Field(None, ge=1, examples=[1])
    fields: dict[str, Any] = Field(..., description="20 field; null bila tidak ditemukan")
    scores: dict[str, float] = Field(default_factory=dict, description="Probabilitas 0-1 per field yang ada nilainya")
    missing_mandatory_fields: list[str] = Field(default_factory=list)


class FinalResult(BaseModel):
    """Isi callback SCORING ketika permintaan selesai."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Apa yang dihasilkan pipeline untuk satu request_id. Dibawa callback SCORING / DONE."
        }
    )

    document_type: str = Field(..., description="Jenis dokumen", examples=["slip_gaji"])
    total_slip: int = Field(..., ge=0, description="Jumlah slip di dokumen ini", examples=[3])
    slips: list[FinalSlip] = Field(
        ...,
        description=(
            "Satu entri per slip. Probabilitas 0-1 dibiarkan apa adanya di sini; pemetaan ke confidence 0/1 "
            "terjadi di kontrak "
            "`extract-ocr`, supaya ambangnya bisa diubah tanpa melatih ulang apa pun"
        ),
    )
    guardrails: GuardrailsPayload | None = Field(
        None, description="Laporan guardrail dari tahap OCR, dikembalikan apa adanya"
    )
    llm_used: bool | None = Field(None, description="true bila arbitrase LLM dipakai saat structuring")


class StageCallback(BaseModel):
    """Isi callback tahap OCR dan STRUCTURING."""

    model_config = ConfigDict(
        json_schema_extra={"description": "Isi callback yang dikirim sebuah tahap ke orchestrator saat selesai."}
    )

    request_id: str = Field(
        ..., description="request_id yang Anda kirim lewat `POST /v1/ekstraksi/jobs`", examples=[REQUEST_ID_EXAMPLE]
    )
    stage: Stage = Field(
        ...,
        description=(
            "Tahap yang statusnya dilaporkan. Biasanya tahap pengirim sendiri; ketika penyerahan ke tahap "
            "BERIKUTNYA gagal setelah beberapa percobaan, pengirim melaporkan `status: FAILED` dengan nama tahap "
            "berikutnya, karena tahap itu tidak pernah menerima jobnya dan tidak bisa melapor sendiri"
        ),
        examples=["OCR"],
    )
    status: Literal["DONE", "FAILED"] = Field(
        ..., description="Hasil tahap ini. `FAILED` di tahap mana pun mengakhiri permintaan", examples=["DONE"]
    )
    result: None = Field(
        None, description="Selalu null untuk OCR dan STRUCTURING; baca hasilnya lewat GET .../jobs/{request_id}"
    )
    error_message: str | None = Field(None, description="Kenapa gagal; null bila `status` `DONE`", examples=[None])
    error_code: str | None = Field(
        None,
        description=(
            "Hanya saat penolakan: `DOWNSTREAM_VALIDATION_ERROR` ketika dokumen ditahan guardrail "
            "(`error_message` memuat alasannya). Tidak ada ketika sebuah tahap rusak"
        ),
        examples=[None],
    )


class ScoringStageCallback(StageCallback):
    """Isi callback SCORING: sama, ditambah hasil akhir."""

    stage: Literal["SCORING"] = Field("SCORING", description="Selalu `SCORING`: tahap terakhir", examples=["SCORING"])
    result: FinalResult | None = Field(  # type: ignore[assignment]
        None, description="Hasil akhir permintaan ketika `status` `DONE`; null ketika `FAILED`"
    )
