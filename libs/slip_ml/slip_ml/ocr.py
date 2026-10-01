"""Tahap OCR: byte dokumen masuk, teks per halaman keluar.

Pembungkus tipis di atas `s1_ocr` yang di-vendor. Dua backend, dan keduanya berakhir di langkah
penyusunan urutan baca yang SAMA, jadi teks yang dilihat tahap berikutnya tidak bergantung pada
mesin mana yang membacanya:

    api        layanan OCR lewat HTTP (paddle6 / PP-OCRv6). **Bawaan.** Tidak ada model di
               dalam image, jadi image-nya kecil dan versinya dikelola di satu tempat.
    rapidocr   model ONNX lokal di dalam proses ini. Perlu paket rapidocr-onnxruntime dan
               menambah ratusan MB pada image; hanya untuk laptop tanpa akses ke layanan OCR.

Alamat layanan OCR datang dari service (environment), bukan dari config.yaml yang ikut di-vendor:
`configure_api()` menuliskannya ke config yang dibaca `core.ocr_api` saat memanggil.
"""

from __future__ import annotations

import time
from typing import Any

from slip_ml.vendor import ensure_path

ensure_path()

import s1_ocr as _ocr  # noqa: E402
from core import config as _config  # noqa: E402

SUPPORTED_SUFFIXES = frozenset(_ocr.SUPPORTED)
BACKEND_API = "api"
BACKEND_LOCAL = "rapidocr"
DEFAULT_BACKEND = BACKEND_API


class UnreadableDocument(ValueError):
    """Byte yang tidak bisa dibaca sebagai PDF maupun gambar."""


class OcrServiceError(RuntimeError):
    """Layanan OCR tidak terjangkau, lambat, atau menjawab bentuk yang tidak dikenal."""


def configure_api(url: str, *, endpoint: str = "/ocr", upload_field: str = "file", timeout: float = 120.0) -> None:
    """Arahkan backend `api` ke layanan OCR ini.

    Ditulis ke `core.config.OCR["api"]` karena modul yang di-vendor membacanya dari sana pada
    setiap panggilan — sehingga service tetap menentukan alamatnya lewat environment tanpa
    perlu menyunting berkas yang di-vendor (suntingan itu akan hilang pada sinkronisasi berikutnya).
    """
    _config.OCR["api"] = {
        "url": url,
        "endpoint": endpoint,
        "upload_field": upload_field,
        "timeout": timeout,
    }


def api_settings() -> dict[str, Any]:
    return dict(_config.OCR.get("api") or {})


class OcrEngine:
    """Satu engine untuk seluruh proses; `read()` aman dipanggil dari threadpool."""

    def __init__(self, backend: str | None = None, *, max_pages: int = 20, render_dpi: int | None = None):
        self.backend = backend or DEFAULT_BACKEND
        self.max_pages = max_pages
        self.render_dpi = render_dpi
        self._service = _ocr.OCRService()

    def warmup(self) -> None:
        """Muat model OCR lokal sekarang, bukan saat permintaan pertama datang.

        Tidak melakukan apa pun untuk backend `api`: tidak ada model di proses ini, dan memanggil
        layanan OCR saat start hanya akan membuat pod gagal hidup ketika layanan itu sedang sibuk.
        """
        if self.backend == BACKEND_LOCAL:
            self._service._load()  # noqa: SLF001 — sengaja: pemanasan adalah urusan service

    def page_count(self, content: bytes) -> int:
        try:
            return _ocr.count_pages(content)
        except Exception as exc:  # PyMuPDF melempar bermacam-macam untuk berkas rusak
            raise UnreadableDocument(str(exc)) from exc

    def read(self, content: bytes, filename: str = "") -> dict[str, Any]:
        """{pages, full_text, engine, model, elapsed_ms} — satu entri `pages` per halaman.

        Halaman dibaca satu per satu dan TIDAK digabung: satu berkas slip gaji lazim memuat
        tiga bulan, dan menggabungkan teksnya mencampur komponen satu bulan ke total bulan
        lain. Tahap structuring yang memutuskan satu halaman = satu slip.
        """
        started = time.perf_counter()
        images = self._render(content, filename)
        pages: list[dict[str, Any]] = []
        confidence: dict[str, Any] = {}
        for index, image in enumerate(images, start=1):
            text, confidence = self._read_page(image)
            pages.append({"page": index, "text": text, "confidence": confidence})
        return {
            "pages": pages,
            "full_text": "\n".join(page["text"] for page in pages),
            "engine": self.backend,
            "model": (confidence or {}).get("engine") if pages else None,
            "n_pages_total": len(images),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    def _read_page(self, image) -> tuple[str, dict[str, Any]]:
        try:
            return self._service.read([image], self.backend)
        except RuntimeError as exc:
            # core.ocr_api melaporkan setiap kegagalan sebagai RuntimeError berisi URL dan status;
            # di sini ia menjadi galat yang bisa dipetakan service ke 503/504, bukan crash 500.
            if self.backend == BACKEND_API:
                raise OcrServiceError(str(exc)) from exc
            raise

    def _render(self, content: bytes, filename: str) -> list:
        if not content:
            raise UnreadableDocument("dokumen kosong")
        try:
            fmt = _ocr.detect_format(content)
        except ValueError as exc:
            raise UnreadableDocument(str(exc)) from exc
        if fmt != "pdf":
            try:
                return [_ocr.load_image(content)]
            except ValueError as exc:
                raise UnreadableDocument(f"tidak bisa membaca '{filename}' sebagai PDF atau gambar") from exc
        try:
            images = _ocr.split_pages(content, self.render_dpi)
        except Exception as exc:
            raise UnreadableDocument(f"tidak bisa membaca '{filename}' sebagai PDF") from exc
        if not images:
            raise UnreadableDocument("PDF tanpa halaman")
        return images[: self.max_pages]
