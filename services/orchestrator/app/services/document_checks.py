"""Pemeriksaan yang dilewati dokumen di sini, sebelum tahap mana pun melihatnya, supaya service di
belakang hanya menilai apa yang memang menjadi tugasnya.

Guardrail TIDAK dipanggil dari sini: ketiga guardrail slip gaji membaca teks OCR (AUC 0,911) dan
bukan piksel (0,638), jadi penilaiannya baru mungkin setelah tahap OCR. Yang tersisa di sini adalah
pemeriksaan murah yang tidak butuh model: jenis berkas, ukuran, dan jumlah halaman."""

import fitz

from ocr_common.errors import TOO_MANY_PAGES, UNREADABLE_FILE, BadRequest
from ocr_common.image_validation import validate_image

from app.config import Settings

PDF_CONTENT_TYPE = "application/pdf"

# Slip gaji yang wajar paling banyak beberapa halaman — bentuk yang paling sering diunggah adalah tiga
# bulan dalam satu berkas. Lebih dari `MAX_DOCUMENT_PAGES` hampir selalu berkas lain yang ikut terbundel.
# Pesannya bisa ditampilkan klien apa adanya.
TOO_MANY_PAGES_MESSAGE = "Jumlah halaman melebihi batas, pastikan hanya mengunggah dokumen slip gaji"


def check_document(content_type: str | None, content: bytes, settings: Settings) -> None:
    """Type and empty file (400), size above `MAX_UPLOAD_BYTES` (413), then for a PDF the page count
    above `MAX_DOCUMENT_PAGES` (400) and an unreadable PDF (400)."""
    validate_image(content_type, content, settings)
    if (content_type or "").lower() == PDF_CONTENT_TYPE and count_pdf_pages(content) > settings.max_document_pages:
        raise BadRequest(TOO_MANY_PAGES_MESSAGE, TOO_MANY_PAGES)


def count_pdf_pages(content: bytes) -> int:
    """The page count from the PDF's page tree, without rendering anything; 400 when it is not a PDF
    PyMuPDF can read (the parser guardrails renders it with) or has no pages."""
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise BadRequest("Uploaded file is not a readable PDF", UNREADABLE_FILE) from exc
    with document:
        if document.page_count == 0:
            raise BadRequest("PDF has no pages", UNREADABLE_FILE)
        return document.page_count
