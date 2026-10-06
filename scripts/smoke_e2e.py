#!/usr/bin/env python3
"""End-to-end smoke test of a running stack, through the orchestrator only, against API spec [07].

    BASE_URL=http://127.0.0.1:8034 API_KEY=... python scripts/smoke_e2e.py [document.pdf|.jpg]

In GKE, run it from a pod that may reach the orchestrator (or through `kubectl port-forward`):

    kubectl -n nilam-ocr-slipgaji port-forward svc/ms-bribrain-nilam-ocr-slipgaji 8034:8034
    BASE_URL=http://127.0.0.1:8034 API_KEY=... python scripts/smoke_e2e.py slip.pdf

Standard library only. Every check prints PASS/FAIL; the exit code is the number of failures. A 202 is
followed up with GET /v1/extract-ocr/{request_id} until the request ends (up to SMOKE_TIMEOUT seconds).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8034").rstrip("/")
API_KEY = os.environ.get("API_KEY", "local-dev-key")
TIMEOUT = float(os.environ.get("SMOKE_TIMEOUT", "120"))
ENVELOPE = {
    "status_code",
    "status_desc",
    "message",
    "data",
    "errors",
    "request_id",
    "guardrails",
    "pipeline_last_stage",
}
FAILURES: list[str] = []


def _minimal_pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


MINIMAL_PDF = _minimal_pdf()


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def _multipart(fields: dict[str, str], file: tuple[str, bytes, str] | None) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    if file:
        name, content, content_type = file
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        )
        parts.append(head.encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def call(method: str, path: str, fields=None, file=None, key: str | None = API_KEY) -> tuple[int, dict]:
    headers = {"X-API-Key": key} if key else {}
    body = None
    if method == "POST":
        body, headers["Content-Type"] = _multipart(fields or {}, file)
    request = urllib.request.Request(BASE_URL + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def submit(document, **fields) -> tuple[int, dict]:
    """POST, and when the answer is 202, GET until it ends."""
    request_id = f"SMOKE_{uuid.uuid4().hex[:12]}"
    status, body = call("POST", "/v1/extract-ocr", {"request_id": request_id, **fields}, document)
    deadline = time.monotonic() + TIMEOUT
    while status == 202 and time.monotonic() < deadline:
        time.sleep(2)
        status, body = call("GET", f"/v1/extract-ocr/{request_id}")
    return status, body


def main() -> int:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        content_type = "application/pdf" if path.suffix.lower() == ".pdf" else "image/jpeg"
        document = (path.name, path.read_bytes(), content_type)
    else:
        # A minimal valid one-page PDF. With EXTRACTION_BACKEND=mock, "3slip" in the name gives three slips;
        # against a real OCR service, pass a real slip gaji as the first argument instead.
        document = ("3slip.pdf", MINIMAL_PDF, "application/pdf")
    print(f"orchestrator {BASE_URL}, document {document[0]}\n")

    status, body = call("GET", "/health", key=None)
    check("health is 200 healthy with the spec shape", status == 200 and set(body) >= {"status", "detail", "version"})

    print("\nfull pipeline")
    status, body = submit(document)
    check("200 with the spec envelope", status == 200 and set(body) == ENVELOPE, f"{status} {body.get('message')}")
    data = body.get("data") or {}
    slips = data.get("slip") or []
    check("data has total_slip and slip[]", data.get("total_slip") == len(slips) > 0, str(data)[:200])
    if slips:
        fields = [value for value in slips[0].values() if isinstance(value, dict)]
        check("every field is {value, confidence 0/1}", fields and all(f.get("confidence") in (0, 1) for f in fields))
    check(
        "guardrails 0 and pipeline_last_stage null (API spec [07]: null on 200)",
        (body.get("guardrails"), body.get("pipeline_last_stage")) == (0, None),
        str((body.get("guardrails"), body.get("pipeline_last_stage"))),
    )

    print("\npipeline_name_sequence")
    for sequence, last, keys in [
        (["guardrails"], "guardrails", {"passed", "document", "checks", "skipped", "unavailable"}),
        (["extraction"], "extraction", {"engine", "full_text", "pages"}),
        (["extraction", "structuring"], "structuring", {"total_slip", "slips"}),
        (["extraction", "structuring", "scoring"], "scoring", {"total_slip", "slip"}),
    ]:
        status, body = submit(document, pipeline_name_sequence=json.dumps(sequence))
        ok = status == 200 and body.get("pipeline_last_stage") is None and keys <= set(body.get("data") or {})
        check(f"{sequence} -> data of {last}", ok, f"{status} {body.get('pipeline_last_stage')} {body.get('message')}")
        if sequence == ["guardrails"] and body.get("data"):
            report = body["data"]
            print(
                f"        guardrails: passed={report.get('passed')} skipped={report.get('skipped')} "
                f"unavailable={report.get('unavailable')} document={report.get('document')}"
            )

    print("\nthresholds")
    status, body = submit(document, column_confidence_threshold='{"all_field": 0.999}')
    slips = (body.get("data") or {}).get("slip") or []
    strict = slips and all(v["confidence"] == 0 for v in slips[0].values() if isinstance(v, dict))
    check("column_confidence_threshold all_field 0.999 -> every confidence 0", status == 200 and bool(strict))
    status, body = submit(document, guardrails_confidence_threshold='{"acc_rej": 0.9999}')
    check(
        "guardrails threshold {acc_rej: 0.9999} -> 400 DOWNSTREAM_VALIDATION_ERROR from guardrails or 200",
        status in (200, 400) and (status == 200 or body.get("pipeline_last_stage") == "guardrails"),
        f"{status} {body.get('message')}",
    )

    print("\nrefusals before anything runs")
    for name, fields, code in [
        ("invalid sequence", {"pipeline_name_sequence": '["extraction","scoring"]'}, "INVALID_PIPELINE_SEQUENCE"),
        ("invalid threshold", {"column_confidence_threshold": '{"nomor_npwp": 0.5}'}, "INVALID_THRESHOLD"),
        ("bare-number guardrails threshold", {"guardrails_confidence_threshold": "0.5"}, "INVALID_THRESHOLD"),
        ("unknown guardrail", {"guardrails_confidence_threshold": '{"accept": 0.5}'}, "INVALID_THRESHOLD"),
        ("wrong document_type", {"document_type": "npwp"}, "UNSUPPORTED_DOCUMENT_TYPE"),
    ]:
        status, body = call("POST", "/v1/extract-ocr", {"request_id": "SMOKE_REFUSE", **fields}, document)
        check(
            f"{name} -> {code}",
            body.get("errors") == code and body.get("pipeline_last_stage") == "orchestrator",
            f"{status} {body.get('errors')}",
        )
    status, body = call("POST", "/v1/extract-ocr", {"request_id": "SMOKE_401"}, document, key="wrong")
    check("wrong API key -> 401 UNAUTHORIZED", status == 401 and body.get("errors") == "UNAUTHORIZED")
    status, body = call(
        "POST",
        "/v1/extract-ocr",
        {"request_id": "SMOKE_413"},
        ("big.pdf", b"%PDF-1.4\n" + b"0" * (3 * 1024 * 1024), "application/pdf"),
    )
    check("3 MB file -> 413 FILE_TOO_LARGE", status == 413 and body.get("errors") == "FILE_TOO_LARGE")
    status, body = call("GET", "/v1/extract-ocr/SMOKE_never_sent")
    check(
        "unknown request_id -> 404 REQUEST_ID_NOT_FOUND", status == 404 and body.get("errors") == "REQUEST_ID_NOT_FOUND"
    )

    print(f"\n{len(FAILURES)} failure(s)")
    return len(FAILURES)


if __name__ == "__main__":
    sys.exit(main())
