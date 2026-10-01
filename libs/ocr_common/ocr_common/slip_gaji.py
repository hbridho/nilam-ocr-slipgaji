"""Apa yang dihasilkan pipeline slip gaji untuk orchestrator: nama field, hasil akhir yang
dibawa callback SCORING, dan pemetaannya ke kontrak `extract-ocr`.

Beda penting dengan dokumen satu-halaman seperti NPWP: **satu berkas slip gaji lazim memuat
beberapa slip** (tiga bulan berturut-turut adalah bentuk yang paling sering diunggah). Karena
itu `data` berisi `total_slip` dan array `slip`, satu entri per halaman, bukan satu himpunan
field datar. Menggabungkannya akan mencampur komponen satu bulan ke total bulan lain.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from ocr_common.types import ContractData, ContractField, ContractSlip, FinalResult

DOCUMENT_TYPE = "slip_gaji"
# `errors` untuk 400 dokumen yang tidak diterima: oleh model guardrail, atau oleh aturan
# structuring yang menolak.
REJECTED_CODE = "DOWNSTREAM_VALIDATION_ERROR"

# Urutan ini yang muncul di `data.slip[]`, dan sama dengan urutan di pipeline penelitian:
# identitas, lalu komponen pendapatan, lalu ketiga total.
SLIP_FIELDS: tuple[str, ...] = (
    "nama_perusahaan",
    "periode",
    "nama_karyawan",
    "nomor_induk_karyawan",
    "jabatan",
    "divisi",
    "status_pegawai",
    "gaji_pokok",
    "tunjangan_jabatan",
    "tunjangan_transport",
    "tunjangan_makan",
    "tunjangan_komunikasi",
    "tunjangan_lain",
    "bonus",
    "insentif",
    "lembur",
    "thr",
    "total_pendapatan",
    "total_potongan",
    "gaji_bersih",
)
MANDATORY_FIELDS: tuple[str, ...] = (
    "nama_perusahaan",
    "nama_karyawan",
    "periode",
    "gaji_pokok",
    "total_pendapatan",
    "gaji_bersih",
)
MONEY_FIELDS: frozenset[str] = frozenset(SLIP_FIELDS[7:])


def contract_data(result: FinalResult, threshold: float, columns: Mapping[str, float] | None = None) -> ContractData:
    """`data` kontrak `extract-ocr`: `total_slip` + satu entri per slip.

    `confidence` bernilai 1 ketika probabilitas model keyakinan (skala 0-1) mencapai ambang
    field itu, selain itu 0. Ambangnya: `columns[field]` (`column_confidence_threshold` dari
    permintaan), lalu `columns["all_field"]`, lalu `threshold` (FIELD_CONFIDENCE_THRESHOLD).
    Field tanpa nilai selalu tetap muncul dengan `value: null` dan `confidence: 0`, supaya
    klien menerima 20 kunci yang sama untuk setiap slip dan tidak perlu menebak mana yang hilang.
    """
    from ocr_common.thresholds import field_threshold

    limits = {name: field_threshold(name, columns, threshold) for name in SLIP_FIELDS}
    slips = [_slip(slip, limits) for slip in result.get("slips") or ()]
    return {"total_slip": len(slips), "slip": slips}


def _slip(slip: Mapping[str, Any], limits: Mapping[str, float]) -> ContractSlip:
    values = slip.get("fields") or {}
    scores = slip.get("scores") or {}
    out: dict[str, Any] = {"page": slip.get("page")}
    for name in SLIP_FIELDS:
        out[name] = _field(values.get(name), scores.get(name), limits[name])
    out["missing_mandatory_fields"] = [name for name in MANDATORY_FIELDS if _blank(values.get(name))]
    return out  # ty: ignore[invalid-return-type]


def ocr_data(ocr: Mapping[str, Any]) -> dict[str, Any]:
    """`data` ketika `pipeline_name_sequence` berhenti di `extraction`: hasil OCR mentah.

    Beda dengan NPWP: layanan OCR slip gaji menyusun teks per halaman dalam urutan baca, jadi yang
    dikembalikan adalah `pages` (teks + ringkasan skor OCR per halaman), bukan `blocks` per baris."""
    return {
        "engine": ocr.get("engine"),
        "model": ocr.get("model"),
        "elapsed_ms": ocr.get("elapsed_ms"),
        "n_pages": ocr.get("n_pages"),
        "full_text": ocr.get("full_text"),
        "pages": [
            {"page": page.get("page"), "text": page.get("text"), "confidence": page.get("confidence") or {}}
            for page in ocr.get("pages") or ()
        ],
    }


def structuring_data(structuring: Mapping[str, Any]) -> dict[str, Any]:
    """`data` ketika `pipeline_name_sequence` berhenti di `structuring`: nilai tiap field per slip beserta
    asalnya, belum ada skor model keyakinan."""
    return {
        "document_type": structuring.get("document_type") or DOCUMENT_TYPE,
        "total_slip": len(structuring.get("slips") or ()),
        "slips": [
            {
                "slip_no": slip.get("slip_no"),
                "page": slip.get("page"),
                "fields": dict(slip.get("fields") or {}),
                "source": dict(slip.get("source") or {}),
                "checks": dict(slip.get("checks") or {}),
                "missing_mandatory_fields": list(slip.get("missing_mandatory_fields") or ()),
            }
            for slip in structuring.get("slips") or ()
        ],
        "llm_used": bool(structuring.get("llm_used")),
        "reject_reason": None,
    }


def guardrails_data(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """`data` ketika `pipeline_name_sequence` hanya `["guardrails"]`: laporan guardrail apa adanya."""
    report = dict(report or {})
    return {
        "passed": bool(report.get("passed")),
        "reason": report.get("reason"),
        "document": report.get("document") or {},
        "pages": report.get("pages") or [],
        "checks": report.get("checks") or {},
        "skipped": report.get("skipped") or [],
        "unavailable": report.get("unavailable") or [],
    }


def _field(value: Any, score: float | None, threshold: float) -> ContractField:
    if _blank(value):
        return {"value": None, "confidence": 0}
    return {"value": value, "confidence": 1 if score is not None and score >= threshold else 0}


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def final_result(
    document_type: str,
    guardrails: dict[str, Any] | None,
    structuring: Mapping[str, Any],
    scoring: Mapping[str, Any],
) -> FinalResult:
    """Gabungan hasil structuring, skor keyakinan, dan laporan guardrail yang ikut dikirim —
    inilah yang dibawa callback SCORING.

    Kedua tahap dicocokkan berdasarkan `slip_no`, bukan urutan daftar: tahap scoring boleh
    melewati slip yang tidak punya satu pun nilai, dan mencocokkan dengan indeks akan
    menggeser skor satu slip ke slip lain tanpa ketahuan.
    """
    by_slip = {entry.get("slip_no"): entry for entry in (scoring.get("slips") or ())}
    slips = []
    for slip in structuring.get("slips") or ():
        number = slip.get("slip_no")
        scored = by_slip.get(number) or {}
        slips.append(
            {
                "slip_no": number,
                "page": slip.get("page"),
                "fields": dict(slip.get("fields") or {}),
                "scores": dict(scored.get("scores") or {}),
                "missing_mandatory_fields": list(slip.get("missing_mandatory_fields") or ()),
            }
        )
    return {
        "document_type": document_type,
        "total_slip": len(slips),
        "slips": slips,
        "guardrails": guardrails,
        "llm_used": bool(structuring.get("llm_used")),
    }


def empty_slip_values() -> dict[str, None]:
    """20 field kosong — dipakai contoh OpenAPI dan tes."""
    return dict.fromkeys(SLIP_FIELDS)


def slip_count(result: Mapping[str, Any] | None) -> int:
    slips: Sequence[Any] = (result or {}).get("slips") or ()
    return len(slips)
