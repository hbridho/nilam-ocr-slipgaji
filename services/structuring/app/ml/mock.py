"""Structurer tiruan untuk tes: satu slip per halaman, nilai tetap, tanpa memuat apa pun.

Hanya dengan ENVIRONMENT=local — hasil karangan tidak boleh sampai ke klien.
"""

from collections.abc import Sequence

from ocr_common.slip_gaji import DOCUMENT_TYPE, SLIP_FIELDS
from ocr_common.types import OcrPage, StructuringResult

VALUES = {
    "nama_perusahaan": "PT SUMBER REJEKI MAKMUR",
    "periode": "2025-02",
    "nama_karyawan": "ANDI SAPUTRA",
    "nomor_induk_karyawan": "202309001",
    "jabatan": "Staff Gudang",
    "gaji_pokok": 4500000,
    "tunjangan_makan": 600000,
    "total_pendapatan": 5100000,
    "total_potongan": 225000,
    "gaji_bersih": 4875000,
}


class MockStructurer:
    name = "mock"

    def structure(self, pages: Sequence[OcrPage]) -> StructuringResult:
        slips = []
        for index, page in enumerate(pages, start=1):
            fields = {name: VALUES.get(name) for name in SLIP_FIELDS}
            slips.append(
                {
                    "slip_no": index,
                    "page": page.get("page") or index,
                    "fields": fields,
                    "source": {name: {"how": "regex", "line": ""} for name in fields if fields[name] is not None},
                    "checks": {},
                    "counts": {"filled": sum(1 for v in fields.values() if v is not None), "by_regex": 10},
                    "llm": {"used": False, "asked": False, "error": None, "compare": {}, "disagree": []},
                    "ocr_text": page.get("text") or "",
                    "ocr_confidence": page.get("confidence") or {},
                    "missing_mandatory_fields": [],
                }
            )
        return {
            "document_type": DOCUMENT_TYPE,
            "slips": slips,
            "n_slips": len(slips),
            "llm_used": False,
            "elapsed_ms": 0.5,
            "reject_reason": None,
        }  # ty: ignore[invalid-return-type]
