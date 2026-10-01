from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from ocr_common.slip_gaji import MANDATORY_FIELDS, MONEY_FIELDS, SLIP_FIELDS

_DESCRIPTIONS = {
    "nama_perusahaan": "Nama perusahaan pemberi kerja, apa adanya termasuk bentuk badan usaha (PT, CV)",
    "periode": "Periode gaji, dinormalkan ke `YYYY-MM`",
    "nama_karyawan": "Nama karyawan",
    "nomor_induk_karyawan": "Nomor induk karyawan / NIK internal perusahaan",
    "jabatan": "Jabatan",
    "divisi": "Divisi / departemen",
    "status_pegawai": "Status kepegawaian (mis. Tetap, Kontrak)",
    "gaji_pokok": "Gaji pokok, rupiah",
    "tunjangan_jabatan": "Tunjangan jabatan, rupiah",
    "tunjangan_transport": "Tunjangan transport, rupiah",
    "tunjangan_makan": "Tunjangan makan, rupiah",
    "tunjangan_komunikasi": "Tunjangan komunikasi, rupiah",
    "tunjangan_lain": "Tunjangan lain-lain, rupiah",
    "bonus": "Bonus, rupiah",
    "insentif": "Insentif, rupiah",
    "lembur": "Upah lembur, rupiah",
    "thr": "Tunjangan hari raya, rupiah",
    "total_pendapatan": "Total pendapatan sebelum potongan, rupiah",
    "total_potongan": "Total potongan, rupiah",
    "gaji_bersih": "Gaji bersih yang diterima; diturunkan bila tidak tercetak (total pendapatan − total potongan)",
}
_EXAMPLES = {
    "nama_perusahaan": "PT SUMBER REJEKI MAKMUR",
    "periode": "2025-02",
    "nama_karyawan": "ANDI SAPUTRA",
    "gaji_pokok": 4500000,
    "total_pendapatan": 6680000,
    "gaji_bersih": 6455000,
}


class ContractField(BaseModel):
    value: Any = Field(
        None,
        description="Nilai yang terbaca; null bila field tidak ditemukan. Nominal uang berupa integer rupiah",
        examples=["PT SUMBER REJEKI MAKMUR"],
    )
    confidence: Literal[0, 1] = Field(
        ...,
        description=(
            "1 ketika probabilitas model keyakinan (0-1) mencapai ambang field ini: `column_confidence_threshold` "
            "permintaan, atau `FIELD_CONFIDENCE_THRESHOLD` (bawaan 0,5); 0 ketika lebih rendah, atau ketika tidak "
            "ada nilainya. Terukur out-of-fold pada ambang 0,80: precision 93,4%, recall 75,6%"
        ),
        examples=[1],
    )


def _field(name: str) -> tuple[type, Any]:
    wajib = " **Wajib**." if name in MANDATORY_FIELDS else ""
    example = _EXAMPLES.get(name, 0 if name in MONEY_FIELDS else None)
    return (
        ContractField,
        Field(..., description=_DESCRIPTIONS[name] + wajib, examples=[{"value": example, "confidence": 1}]),
    )


# 20 field ditulis dari `slip_gaji.SLIP_FIELDS`, bukan diketik ulang: daftar yang diketik ulang akan
# bergeser diam-diam begitu pipeline menambah field.
Slip = create_model(
    "Slip",
    page=(int | None, Field(None, description="Halaman asal slip ini", examples=[1])),
    **{name: _field(name) for name in SLIP_FIELDS},
    missing_mandatory_fields=(
        list[str],
        Field(
            default_factory=list,
            description="Field wajib yang tidak terisi pada slip ini; daftar kosong berarti lengkap",
            examples=[[]],
        ),
    ),
)
Slip.__doc__ = "Satu slip gaji: 20 field ber-`{value, confidence}`, halamannya, dan field wajib yang kosong."


class SlipGajiData(BaseModel):
    total_slip: int = Field(
        ...,
        ge=0,
        description="Jumlah slip di dokumen ini. Satu berkas lazim memuat tiga bulan berturut-turut",
        examples=[3],
    )
    slip: list[Slip] = Field(  # ty: ignore[invalid-type-form]
        ...,
        description=("Satu entri per slip, urut halaman. Setiap entri berdiri sendiri: totalnya milik bulan itu saja"),
    )


class ExtractOcrResponse(BaseModel):
    status_code: int = Field(..., description="Sama dengan kode status HTTP", examples=[200])
    status_desc: str = Field(..., description="Frasa standar HTTP untuk `status_code`", examples=["OK"])
    message: str = Field(
        ...,
        description="Untuk dibaca manusia; kalimatnya bisa berubah, jadi bercabanglah pada `errors`",
        examples=["OCR extraction completed successfully"],
    )
    data: SlipGajiData | dict[str, Any] | None = Field(
        None,
        description=(
            "Pipeline penuh (atau berakhir di `scoring`): `{total_slip, slip[]}`, tiap field `{value, "
            "confidence 0/1}`. "
            'Berakhir lebih awal: hasil service terakhir apa adanya — `["guardrails"]` laporan guardrail '
            "`{passed, reason, document, pages, checks, skipped, unavailable}`; `extraction` hasil OCR "
            "`{engine, model, elapsed_ms, n_pages, full_text, pages}`; `structuring` "
            "`{document_type, total_slip, slips, llm_used, reject_reason}`. Null bila gagal atau masih diproses"
        ),
    )
    errors: str | None = Field(
        None, description="Kode kegagalan ketika permintaan gagal atau ditolak; null selain itu", examples=[None]
    )
    request_id: str | None = Field(None, description="request_id milik respons ini")
    guardrails: Literal[0, 1] | None = Field(
        None,
        description=(
            "1 = ditolak (ketiga guardrail, atau aturan structuring), 0 = tidak ditolak, null = masih diproses "
            "atau error sebelum dokumen dinilai"
        ),
        examples=[0],
    )
    pipeline_last_stage: Literal["orchestrator", "guardrails", "extraction", "structuring", "scoring"] | None = Field(
        None,
        description=(
            "Service asal jawaban, dengan nama yang sama seperti di `pipeline_name_sequence`: service yang "
            "menyelesaikan, menolak, gagal, atau sedang berjalan — atau `orchestrator` bila permintaan ditolak "
            "pintu masuk sendiri sebelum satu pun service pipeline dipanggil (kunci API, berkas, parameter, "
            "request_id tidak dikenal pada `GET`). Penolakan guardrail-blank / -blur / -identity dilaporkan "
            "sebagai `guardrails`."
        ),
        examples=["scoring"],
    )
    params: Any = Field(
        None,
        description=(
            "`params` yang dikirim bersama `POST /v1/extract-ocr`, dikembalikan apa adanya; null bila tidak "
            "dikirim. Tidak disimpan, jadi selalu null pada `GET /v1/extract-ocr/{request_id}`"
        ),
        examples=[{"nik": "3123456711950001", "refno": "PK19039Y8U"}],
    )
