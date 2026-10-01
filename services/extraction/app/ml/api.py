"""Mesin OCR lewat layanan HTTP (paddle6 / PP-OCRv6) — jalur bawaan.

Tidak ada model di dalam image ini: halaman dirender di sini, lalu dikirim satu per satu ke
layanan OCR, dan kotak jawabannya disusun ulang menjadi urutan baca oleh kode yang sama dengan
jalur lokal. Teks yang dilihat tahap structuring karena itu tidak bergantung pada mesin mana yang
membacanya.

Kegagalan layanan OCR dipetakan ke status yang benar: tidak terjangkau -> 503, tidak menjawab
tepat waktu -> 504. Keduanya aman diulang, dan itu yang membedakannya dari dokumen yang memang
tidak bisa dibaca (400).
"""

import re

from ocr_common.errors import BadRequest, InternalError, UpstreamTimeout, UpstreamUnavailable
from ocr_common.types import OcrEngineResult

from slip_ml.ocr import BACKEND_API, OcrServiceError, UnreadableDocument, configure_api
from slip_ml.ocr import OcrEngine as _Engine

# Layanan menerima sambungan tetapi tidak menjawab tepat waktu -> 504. Perhatikan bahwa `ConnectTimeout`
# BUKAN ini: gagal menyambung berarti tidak terjangkau (503), dan keduanya sama-sama memuat kata
# "timeout", jadi membedakannya hanya dengan kata itu akan salah melabeli layanan yang mati.
READ_TIMEOUT = re.compile(r"ReadTimeout|read timed out", re.I)
HTTP_STATUS = re.compile(r"-> HTTP (\d{3})")


class ApiOcrEngine:
    name = "api"

    def __init__(
        self,
        url: str,
        *,
        endpoint: str = "/ocr",
        upload_field: str = "file",
        timeout: float = 120.0,
        max_pages: int = 20,
        render_dpi: int | None = None,
    ):
        if not url:
            raise RuntimeError("EXTRACTION_OCR_URL wajib diisi ketika EXTRACTION_BACKEND=api")
        configure_api(url, endpoint=endpoint, upload_field=upload_field, timeout=timeout)
        self.url = url
        self.timeout = timeout
        self._engine = _Engine(BACKEND_API, max_pages=max_pages, render_dpi=render_dpi)

    def warmup(self) -> None:
        """Tidak ada yang dimuat: modelnya ada di layanan OCR, bukan di sini."""

    def read(self, filename: str, content: bytes) -> OcrEngineResult:
        try:
            result = self._engine.read(content, filename)
        except UnreadableDocument as exc:
            # Dokumen yang tidak terbaca adalah masalah dokumennya, bukan service ini.
            raise BadRequest(str(exc)) from exc
        except OcrServiceError as exc:
            raise self._as_service_error(str(exc)) from exc
        return {
            "pages": result["pages"],
            "full_text": result["full_text"],
            "model": result.get("model"),
            "engine": self.name,
            "elapsed_ms": result["elapsed_ms"],
            "n_pages": len(result["pages"]),
        }  # ty: ignore[invalid-return-type]

    def _as_service_error(self, message: str):
        """Kegagalan layanan OCR menjadi status yang bisa ditindaklanjuti pemanggil.

        503 dan 504 aman diulang; 500 tidak, karena itu berarti permintaan kita sendiri yang salah
        bentuk (endpoint keliru, nama field unggah tidak cocok) dan mengulangnya akan gagal lagi.
        """
        if READ_TIMEOUT.search(message):
            return UpstreamTimeout(f"layanan OCR tidak menjawab dalam {self.timeout:g}s")
        status = HTTP_STATUS.search(message)
        if status:
            code = int(status.group(1))
            if code >= 500:
                return UpstreamUnavailable(f"layanan OCR menjawab HTTP {code}")
            return InternalError(f"layanan OCR menolak permintaan ini (HTTP {code}); periksa EXTRACTION_OCR_* ")
        if "unreadable JSON" in message:
            return InternalError("layanan OCR menjawab bentuk yang tidak dikenali")
        return UpstreamUnavailable(f"layanan OCR tidak terjangkau: {message}")
