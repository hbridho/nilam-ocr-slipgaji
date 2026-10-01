#!/usr/bin/env python3
"""OCR via the external HTTP service (paddle6 OCR API), as an alternative to local
RapidOCR.

    from core import ocr_api
    dets = ocr_api.read_page(png_bytes, name="page1.png")
    #  -> [[poly, text, score], ...]   (same shape RapidOCR yields)

s1_ocr.py calls this when `ocr.backend: api`, then runs the SAME reading-order
reconstruction it uses for RapidOCR — so the text a model sees does not depend on
which engine produced it.

Config lives in config.yaml under `ocr.api`: the base URL, the POST path (`/ocr`),
the multipart field name (`file`) and a timeout. The reply shape is fixed by the
service and handled in `_detections` below.

    POST /ocr           multipart file=<image|pdf>   ->  {num_pages, pages:[{texts:[{text,score,poly}]}]}
    POST /ocr?split=1   a PDF                         ->  [ {pages:[...]}, ... ]  one object per page
"""

import io
from typing import Any

from core import config

_SESSION = None


def _session():
    """A requests session that ignores the environment's proxy — the OCR service is
    on a private 10.x address a corporate proxy would try (and fail) to route."""
    global _SESSION
    if _SESSION is None:
        import requests

        _SESSION = requests.Session()
        _SESSION.trust_env = False
    return _SESSION


def _detections(payload: Any) -> list:
    """Pull `[[poly, text, score], ...]` out of a paddle6 OCR reply.

    `poly` is four [x, y] points, exactly what RapidOCR returns, so the caller can
    feed the list straight into the reading-order step.
    """
    if isinstance(payload, list):  # /ocr?split=1
        pages = [pg for obj in payload if isinstance(obj, dict) for pg in (obj.get("pages") or [])]
    elif isinstance(payload, dict):  # /ocr
        pages = payload.get("pages") or []
    else:
        raise RuntimeError(f"OCR API: unexpected reply {type(payload).__name__}")

    dets = []
    for pg in pages:
        for t in pg.get("texts") or []:
            txt = (t.get("text") or "").strip()
            if txt:
                dets.append([t.get("poly") or [], txt, float(t.get("score") or 0.0)])
    return dets


def read_page(image_bytes: bytes, name: str = "page.png", content_type: str = "image/png") -> list:
    """Send one page image to the OCR service; return its detections as
    `[[poly, text, score], ...]`.

    Raises RuntimeError with the URL and status on any failure, so the caller can
    report it and fall back rather than crash.
    """
    cfg = config.OCR.get("api") or {}
    base = (cfg.get("url") or "").rstrip("/")
    if not base:
        raise RuntimeError("ocr.api.url is not set in config.yaml")
    endpoint = cfg.get("endpoint") or "/ocr"
    field = cfg.get("upload_field") or "file"
    timeout = cfg.get("timeout", 120)
    url = base + (endpoint if endpoint.startswith("/") else "/" + endpoint)

    try:
        resp = _session().post(url, files={field: (name, io.BytesIO(image_bytes), content_type)}, timeout=timeout)
    except Exception as exc:
        raise RuntimeError(f"OCR API {url} unreachable: {type(exc).__name__}: {exc}") from exc

    if resp.status_code != 200:
        raise RuntimeError(f"OCR API {url} -> HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"OCR API {url} returned unreadable JSON: {resp.text[:200]}") from exc

    return _detections(payload)


def health() -> dict:
    """GET /health — model list and load state. For a quick reachability check."""
    base = ((config.OCR.get("api") or {}).get("url") or "").rstrip("/")
    if not base:
        raise RuntimeError("ocr.api.url is not set in config.yaml")
    return _session().get(base + "/health", timeout=15).json()
