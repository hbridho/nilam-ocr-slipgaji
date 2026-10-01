#!/usr/bin/env python3
"""Stage 1 of the pipeline: a payslip's bytes in, its OCR text out.

    detect PDF vs image  ->  render every page @ config DPI  ->  OCR  ->  text

Two OCR backends, chosen by `ocr.backend` in config.yaml:

    rapidocr   local ONNX models, detections rebuilt into reading order (default)
    api        an HTTP OCR service — core/ocr_api.py

Nothing here knows what a payslip field is. That is deliberate: this stage produces
text, s2_structure.py turns text into the 20 fields, and the seam between them is this
module's public surface — `detect_format`, `count_pages`, `render_page`, `split_pages`,
`load_image`, and `OCRService`.

The PyMuPDF text layer is not read. A 150-document sample of this corpus found a usable
text layer in 6 of them; `fitz` is here for rendering only.

Settings (backend, DPI, RapidOCR options, the API URL) come from config.yaml via core.config.
"""

import io

import fitz  # PyMuPDF — rendering only; the text layer is no longer read
from core import config
from PIL import Image

RENDER_DPI = config.OCR["render_dpi"]  # PDF -> image, for OCR
RAPIDOCR_PARAMS = config.OCR.get("rapidocr") or {}  # extra kwargs for RapidOCR(**...)
SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}


# ── Format detection and rendering ─────────────────────────────────────────


def detect_format(data: bytes) -> str:
    """PDF or plain image, decided by the file's magic bytes."""
    if not data:
        raise ValueError("empty document")
    return "pdf" if data[:5] == b"%PDF-" else "image"


def _to_image(pixmap) -> Image.Image:
    return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")


def load_image(data: bytes) -> Image.Image:
    """Open raw image bytes as an RGB image, or raise ValueError if unreadable."""
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise ValueError("could not read the bytes as an image") from exc


def count_pages(data: bytes) -> int:
    """How many pages the document has, without rendering any of them."""
    if detect_format(data) != "pdf":
        return 1
    with fitz.open(stream=data, filetype="pdf") as doc:
        return len(doc)


def render_page(data: bytes, page_no: int, dpi: int = None):
    """Render a single PDF page (1-based), or None if it doesn't exist."""
    dpi = dpi or RENDER_DPI
    with fitz.open(stream=data, filetype="pdf") as doc:
        if not 1 <= page_no <= len(doc):
            return None
        return _to_image(doc[page_no - 1].get_pixmap(dpi=dpi))


def split_pages(data: bytes, dpi: int = None) -> list:
    """Render every PDF page to an RGB image."""
    dpi = dpi or RENDER_DPI
    with fitz.open(stream=data, filetype="pdf") as doc:
        return [_to_image(page.get_pixmap(dpi=dpi)) for page in doc]


# ── OCR Service ───────────────────────────────────────────────────────────


class OCRService:
    """Turns page images into text. Two backends, chosen by `ocr.backend` in config.yaml:

        rapidocr   local ONNX models, rebuilt into reading order (default)
        api        an HTTP OCR service (core.ocr_api)

    Built once and reused for every document — the RapidOCR engine is expensive.
    """

    def __init__(self):
        self._engine = None

    def _load(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR(**RAPIDOCR_PARAMS)
        return self._engine

    def read(self, images: list, backend: str = None):
        """(text, confidence) — the reading-order text plus a summary of the OCR engine's
        per-box confidence scores. Both backends return scores; see `_confidence`."""
        backend = backend or config.OCR.get("backend", "rapidocr")
        if backend == "api":
            return self._read_api(images)
        return self._read_rapidocr(images)

    # Both backends end at the same _reading_order step, so the text a model sees is
    # independent of which engine produced the detections.

    def _read_rapidocr(self, images: list):
        import numpy as np

        engine = self._load()
        out, line_scores, dets = [], [], []
        for img in images:
            result, _ = engine(np.asarray(img))
            if result:
                for text, score in self._reading_order_scored(result):
                    out.append(text)
                    line_scores.append(score)
                dets.extend(result)
        return "\n".join(out), self._confidence(dets, "rapidocr", line_scores)

    def _read_api(self, images: list):
        """Send each page image to the external OCR service, then rebuild reading order
        from its detection boxes — the same last step as the RapidOCR path."""
        from core import ocr_api

        out, line_scores, dets = [], [], []
        for i, img in enumerate(images, 1):
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page = ocr_api.read_page(buf.getvalue(), name=f"page{i}.png")
            if page:
                for text, score in self._reading_order_scored(page):
                    out.append(text)
                    line_scores.append(score)
                dets.extend(page)
        return "\n".join(out), self._confidence(dets, "api", line_scores)

    @staticmethod
    def _confidence(dets: list, engine: str, line_scores: list = None) -> dict:
        """Summarise the per-box confidence RapidOCR / the Paddle API returns.

        `dets` is `[[poly, text, score], ...]`. score is 0..1 — how sure the recogniser
        was of that box's characters. The summary travels with the OCR text into the
        detail JSON so a low-confidence page can be spotted without re-running anything.

        `lines` rides along index-aligned with `ocr_text.splitlines()`, so a field whose
        provenance names a line number (`source[field].line_no`) can inherit that line's
        score. That is what the OCR Score Binning table bins on.
        """
        pairs = [((r[1] or "").strip(), float(r[2])) for r in dets if len(r) > 2 and r[2] is not None]
        # a line whose boxes carried no score stays None rather than becoming a fake 0
        lines = [None if s is None else round(float(s), 4) for s in (line_scores or [])]
        if not pairs:
            return {
                "engine": engine,
                "n_boxes": len(dets),
                "mean": None,
                "min": None,
                "p10": None,
                "n_low": 0,
                "worst": [],
                "lines": lines,
            }
        scores = sorted(s for _, s in pairs)
        return {
            "engine": engine,
            "n_boxes": len(pairs),
            "mean": round(sum(scores) / len(scores), 4),
            "min": round(scores[0], 4),
            "p10": round(scores[max(0, round(len(scores) * 0.10) - 1)], 4),
            "n_low": sum(1 for s in scores if s < 0.90),
            "worst": [{"text": t, "score": round(s, 4)} for t, s in sorted(pairs, key=lambda p: p[1])[:5]],
            # index-aligned with ocr_text.splitlines() — what the binning table bins on
            "lines": lines,
        }

    @classmethod
    def _reading_order(cls, result) -> list:
        """The reading-order lines alone — the shape callers that only want text expect."""
        return [text for text, _ in cls._reading_order_scored(result)]

    @staticmethod
    def _reading_order_scored(result) -> list:
        """Rebuild reading order from the detection boxes, as [(line, score), ...].

        This is NOT a parser — it is the last step of reading the image, and the model
        depends on it. RapidOCR returns detections in its own order, which interleaves the
        columns of a two-column table: on one payslip "250.000" came back directly under
        "lembur" when it belonged to "Uang Makan" two rows further down. Hand that to a
        language model and it will confidently report overtime pay.

        Grouping detections into rows by their vertical centre and sorting left-to-right
        within each row restores the printed layout, so what the model reads looks like
        what a person would see.

        A line's score is the MINIMUM of its boxes, not the mean: one mangled number in an
        otherwise clean row is exactly what the score has to expose, and a mean would bury
        it under the confident label beside it.
        """
        boxes = []
        for row in result:
            xs = [p[0] for p in row[0]]
            ys = [p[1] for p in row[0]]
            boxes.append(
                {
                    "text": row[1],
                    "x": min(xs),
                    "y": (min(ys) + max(ys)) / 2,
                    "h": max(ys) - min(ys),
                    "s": float(row[2]) if len(row) > 2 and row[2] is not None else None,
                }
            )
        boxes.sort(key=lambda b: b["y"])

        lines, current = [], []
        for box in boxes:
            if current:
                mean_y = sum(b["y"] for b in current) / len(current)
                mean_h = sum(b["h"] for b in current) / len(current)
                # A detection belongs to the current row while its centre stays within
                # about half a line height of it.
                if abs(box["y"] - mean_y) > max(mean_h * 0.6, 6):
                    lines.append(current)
                    current = []
            current.append(box)
        if current:
            lines.append(current)

        # Two spaces between cells, so a column gap survives into the prompt.
        out = []
        for line in lines:
            cells = sorted(line, key=lambda b: b["x"])
            got = [b["s"] for b in cells if b["s"] is not None]
            out.append(("  ".join(b["text"] for b in cells), min(got) if got else None))
        return out
