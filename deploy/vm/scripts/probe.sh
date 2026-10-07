#!/usr/bin/env bash
# End-to-end check after a deploy, with no customer data: two synthetic PDFs through the orchestrator (a blank page,
# which the blank guardrail must refuse with 400, and a made-up slip, which must pass), then their rows in
# nilam_ocr_slipgaji.nilam_guardrails_results (3 each: blank, blur, identity). Run as root on the VM, from anywhere:
#
#   deploy/vm/scripts/probe.sh
#
# The made-up slip goes through the LLM when ENABLE_LLM=true (one Bedrock call). No secret is printed.
set -euo pipefail
cd "$(dirname "$0")/.."
C="docker compose -f docker-compose.yml -f docker-compose.build.yml"
O=ms-bribrain-nilam-ocr-slipgaji-orchestrator
STAMP=$(date +%s)

echo "== containers"
docker ps --filter name=slipgaji --format '{{.Names}}  |  {{.Status}}'

echo "== two synthetic documents through the orchestrator"
docker exec -i -e STAMP="$STAMP" "$O" python - <<'PY'
import json, os, time, urllib.request, uuid
import pymupdf  # in the orchestrator image

stamp = os.environ["STAMP"]
def pdf(lines):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for i, line in enumerate(lines):
        page.insert_text((60, 80 + i * 24), line, fontsize=14)
    return doc.tobytes()

slip = pdf(["SLIP GAJI KARYAWAN", "PT CONTOH SEJAHTERA ABADI", "Periode : Maret 2025", "Nama : BUDI CONTOH",
            "NIK Karyawan : 000123", "Jabatan : Staf Administrasi", "PENDAPATAN",
            "Gaji Pokok                Rp 5.000.000", "Tunjangan Transport       Rp 400.000",
            "Tunjangan Makan           Rp 300.000", "Total Pendapatan          Rp 5.700.000", "POTONGAN",
            "BPJS Kesehatan            Rp 50.000", "BPJS Ketenagakerjaan      Rp 100.000",
            "Total Potongan            Rp 150.000", "GAJI BERSIH               Rp 5.550.000"])

def post(rid, name, content, expect):
    b = uuid.uuid4().hex
    body = (f'--{b}\r\nContent-Disposition: form-data; name="request_id"\r\n\r\n{rid}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="document_type"\r\n\r\nslip_gaji\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/pdf\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request("http://127.0.0.1:8034/v1/extract-ocr", data=body, method="POST",
                                 headers={"X-API-Key": os.environ["API_KEY"],
                                          "Content-Type": f"multipart/form-data; boundary={b}"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            status, answer = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        status, answer = e.code, json.loads(e.read() or b"{}")
    ok = "OK" if status in expect else f"UNEXPECTED (want {expect})"
    print(f"  {rid}: HTTP {status} in {time.monotonic() - started:.1f}s {ok}  "
          f"message={(answer.get('message') or '')[:80]!r}")

post(f"PROBE_BLANK_{stamp}", "blank.pdf", pdf([]), (400,))
post(f"PROBE_SLIP_{stamp}", "contoh.pdf", slip, (200, 202))
PY
sleep 3

echo "== their rows in nilam_ocr_slipgaji.nilam_guardrails_results (expect 6)"
$C --profile migrate run --rm -T -e STAMP="$STAMP" migrate python - <<'PY' 2>&1 | grep -v "^INFO\| Container " || true
import asyncio, os
from sqlalchemy import text
from ocr_common.pipeline.cloudsql import config_from, register
from ocr_common.pipeline.database import dispose_engines, get_engine
url = register(config_from({k.lower(): v for k, v in os.environ.items()}))
async def main():
    async with get_engine(url).connect() as conn:
        rows = (await conn.execute(text(
            "SELECT request_id, guardrail, passed, verdict, confidence, threshold FROM "
            "nilam_ocr_slipgaji.nilam_guardrails_results WHERE request_id LIKE :p ORDER BY id"),
            {"p": f"PROBE_%_{os.environ['STAMP']}"})).all()
    await dispose_engines()
    print(f"  {len(rows)} rows")
    for r in rows:
        print("  " + " | ".join("-" if v is None else str(v) for v in r))
asyncio.run(main())
PY
