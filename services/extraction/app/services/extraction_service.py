from collections.abc import Mapping
from typing import Any

from starlette.concurrency import run_in_threadpool

from ocr_common.clients.guardrails import GuardrailsFanout
from ocr_common.image_validation import validate_image

from app.config import Settings


def ocr_summary(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Satu ringkasan mutu OCR untuk seluruh dokumen, dari ringkasan per halaman.

    Guardrail menilai teks dokumen utuh, jadi ia butuh satu angka per ciri. Jumlah kotak dan kotak
    berskor rendah dijumlahkan; skor rata-rata ditimbang jumlah kotak (halaman berisi 3 kotak tidak
    boleh menarik rata-rata sekuat halaman berisi 90); skor minimum diambil yang terburuk, karena
    satu halaman yang hancur sudah cukup membuat dokumen tidak terpakai.
    """
    boxes = low = 0
    weighted = 0.0
    worst = None
    for page in pages:
        confidence = page.get("confidence") or {}
        n = int(confidence.get("n_boxes") or 0)
        mean = confidence.get("mean")
        minimum = confidence.get("min")
        boxes += n
        low += int(confidence.get("n_low") or 0)
        if mean is not None and n:
            weighted += float(mean) * n
        if minimum is not None:
            worst = float(minimum) if worst is None else min(worst, float(minimum))
    return {
        "n_boxes": boxes,
        "n_low": low,
        "mean": round(weighted / boxes, 4) if boxes else None,
        "min": worst,
    }


def accept_thresholds(stored: Mapping[str, Any] | None) -> dict[str, float] | None:
    """Ambang per guardrail `{nama: x}` (sisi accept) dari input job. Job yang disimpan sebelum format objek
    (6 Okt 2026) membawa `{nama: {"value": x, "target": "accept" | "reject"}}`; job itu masih bisa
    dijalankan ulang setelah rilis, jadi bentuk lama diterjemahkan: target reject x = accept 1 - x."""
    if not stored:
        return None
    thresholds = {}
    for name, value in stored.items():
        if isinstance(value, Mapping):
            old = value.get("value")
            if old is None:
                continue
            value = 1 - float(old) if value.get("target") == "reject" else old
        thresholds[name] = float(value)
    return thresholds or None


class ExtractionService:
    """Tahap OCR: baca dokumen, lalu — bila `guardrails` ada di `pipeline_name_sequence` — minta ketiga
    guardrail menilai teksnya, bersamaan.

    Urutannya OCR dulu, guardrail sesudahnya, karena model guardrail slip gaji membaca teks OCR.
    Dokumen yang ditahan tetap menyimpan hasil OCR-nya (jobnya DONE dengan `reject_reason`), jadi
    penolakan masih bisa ditelusuri tanpa menjalankan ulang OCR.
    """

    def __init__(self, engine, settings: Settings, guardrails: GuardrailsFanout | None = None):
        self._engine = engine
        self._settings = settings
        self._guardrails = guardrails

    async def extract(
        self,
        filename: str,
        content_type: str | None,
        content: bytes,
        *,
        request_id: str = "",
        run_guardrails: bool = True,
        guardrail_thresholds: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_image(content_type, content, self._settings)
        result: dict[str, Any] = dict(await run_in_threadpool(self._engine.read, filename, content))
        if not run_guardrails or self._guardrails is None:
            result["guardrails"] = None
            return result
        # Dokumen tanpa teks TIDAK digagalkan di sini: itu jawaban guardrail `blank`, dan nasabah butuh
        # alasannya ("pastikan halaman yang diunggah benar"), bukan galat 400.
        report = await self._guardrails.check(
            request_id,
            result.get("full_text") or "",
            ocr_summary(result.get("pages") or []),
            n_pages=len(result.get("pages") or ()),
            thresholds=accept_thresholds(guardrail_thresholds),
        )
        result["guardrails"] = report
        if not report.get("passed", True):
            # Bukan exception: hasil OCR-nya sah dan tetap disimpan. `reject_reason` yang membuat
            # pipeline berhenti di sini dan orchestrator menjawab 400 dengan guardrails: 1.
            result["reject_reason"] = report.get("reason")
        return result
