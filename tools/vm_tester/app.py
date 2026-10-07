"""Local tester for the slip gaji stack on the VM (gc-bribrain-dev-gce-facematch-01).

Runs on the laptop, calls the VM's orchestrator through a tunnel (VS Code Remote-SSH port forward, or
`gcloud compute start-iap-tunnel`) that makes it reachable at VM_BASE_URL (default http://127.0.0.1:18034).
The API key stays here: the browser only talks to this app, this app adds X-API-Key.

    python app.py            # then open http://127.0.0.1:8099
"""

import os
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

HERE = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    """KEY=VALUE lines of .env into the environment (already-set variables win)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_env(HERE / ".env")
VM_BASE_URL = os.environ.get("VM_BASE_URL", "http://127.0.0.1:18034").rstrip("/")
VM_API_KEY = os.environ.get("VM_API_KEY", "")
PORT = int(os.environ.get("TESTER_PORT", "8099"))
TIMEOUT = float(os.environ.get("VM_TIMEOUT_SECONDS", "60"))

app = FastAPI(title="Slip gaji VM tester", docs_url=None, redoc_url=None)


def _headers() -> dict[str, str]:
    return {"X-API-Key": VM_API_KEY} if VM_API_KEY else {}


def _answer(response: httpx.Response, started: float) -> JSONResponse:
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:2000]}
    return JSONResponse(
        {"http_status": response.status_code, "elapsed_ms": round((time.monotonic() - started) * 1000), "body": body}
    )


def _unreachable(exc: Exception, started: float) -> JSONResponse:
    return JSONResponse(
        {
            "http_status": None,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "body": {
                "error": f"{type(exc).__name__}: {exc}",
                "hint": f"Is the tunnel up? {VM_BASE_URL} must reach the VM's orchestrator (port 8034).",
            },
        },
        status_code=502,
    )


@app.get("/api/config")
async def config() -> dict:
    return {"vm_base_url": VM_BASE_URL, "api_key_set": bool(VM_API_KEY)}


@app.get("/api/health")
async def health() -> JSONResponse:
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            return _answer(await client.get(f"{VM_BASE_URL}/health"), started)
    except httpx.HTTPError as exc:
        return _unreachable(exc, started)


@app.post("/api/extract")
async def extract(
    file: UploadFile = File(...),
    request_id: str = Form(""),
    pipeline_name_sequence: str = Form(""),
    guardrails_confidence_threshold: str = Form(""),
    column_confidence_threshold: str = Form(""),
) -> JSONResponse:
    fields = {"request_id": request_id.strip() or f"TEST_{uuid.uuid4().hex[:12]}"}
    for name, value in (
        ("pipeline_name_sequence", pipeline_name_sequence),
        ("guardrails_confidence_threshold", guardrails_confidence_threshold),
        ("column_confidence_threshold", column_confidence_threshold),
    ):
        if value.strip():
            fields[name] = value.strip()
    content = await file.read()
    files = {"file": (file.filename or "slip.pdf", content, file.content_type or "application/pdf")}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(f"{VM_BASE_URL}/v1/extract-ocr", data=fields, files=files, headers=_headers())
    except httpx.HTTPError as exc:
        return _unreachable(exc, started)
    answer = _answer(response, started)
    answer.headers["X-Request-Id"] = fields["request_id"]
    return answer


@app.get("/api/result/{request_id}")
async def result(request_id: str) -> JSONResponse:
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            return _answer(await client.get(f"{VM_BASE_URL}/v1/extract-ocr/{request_id}", headers=_headers()), started)
    except httpx.HTTPError as exc:
        return _unreachable(exc, started)


PAGE = """<!doctype html>
<html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Slip gaji VM tester</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#667085;--line:#e3e6ea;--ok:#127a3c;--bad:#b42318;--warn:#a15c07;--acc:#1f5eff}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--card:#1d2026;--fg:#e8eaee;--muted:#9aa3b2;--line:#2c3038;--ok:#4cc27a;--bad:#ff7a6e;--warn:#e7a948;--acc:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px}h1{font-size:20px;margin:0 0 4px}.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:14px 0}
label{display:block;font-weight:600;margin:10px 0 4px}input[type=text]{width:100%;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:9px 16px;font-weight:600;cursor:pointer}button:disabled{opacity:.5}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font-weight:600;font-size:12px;border:1px solid currentColor}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left}
pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto;max-height:420px;font-size:12px}
.seq label{display:inline;font-weight:400;margin-right:12px}
</style></head><body><main>
<h1>Slip gaji · VM tester</h1>
<div class="muted">Mengirim ke orchestrator di VM lewat tunnel: <code id="vmurl"></code> · API key: <span id="keyset"></span></div>

<div class="card"><b>Status VM</b> <span id="health" class="pill">memeriksa…</span>
 <button id="recheck" type="button" style="margin-left:10px;padding:4px 10px">Cek lagi</button>
 <pre id="healthbody" hidden></pre></div>

<form id="f" class="card">
 <label>Berkas slip gaji (PDF/JPG/PNG, maks 2,5 MB)</label><input type="file" name="file" required>
 <div class="row">
  <div><label>request_id (kosong = otomatis)</label><input type="text" name="request_id" placeholder="TEST_…"></div>
  <div><label>guardrails_confidence_threshold</label><input type="text" name="guardrails_confidence_threshold" placeholder='{"acc_rej": 0.5}'></div>
  <div><label>column_confidence_threshold</label><input type="text" name="column_confidence_threshold" placeholder='{"all_field": 0.5}'></div>
 </div>
 <label>pipeline_name_sequence</label>
 <div class="seq"><label><input type="checkbox" value="guardrails" checked> guardrails</label><label><input type="checkbox" value="extraction" checked> extraction</label><label><input type="checkbox" value="structuring" checked> structuring</label><label><input type="checkbox" value="scoring" checked> scoring</label></div>
 <p><button id="send">Kirim ke VM</button> <span id="state" class="muted"></span></p>
</form>

<div class="card" id="out" hidden>
 <div><b>Jawaban</b> <span id="code" class="pill"></span> <span id="meta" class="muted"></span></div>
 <div id="summary"></div>
 <div id="slips"></div>
 <details><summary>JSON lengkap</summary><pre id="json"></pre></details>
</div>
</main>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function loadConfig(){const c=await (await fetch("/api/config")).json();$("vmurl").textContent=c.vm_base_url;
 $("keyset").innerHTML=c.api_key_set?'<span class="ok">terisi</span>':'<span class="bad">KOSONG (isi VM_API_KEY di .env)</span>';}
async function health(){const h=$("health");h.textContent="memeriksa…";h.className="pill";
 const r=await (await fetch("/api/health")).json();$("healthbody").hidden=false;$("healthbody").textContent=JSON.stringify(r.body,null,2);
 const ok=r.http_status===200&&r.body.status==="healthy";h.textContent=ok?"healthy":(r.http_status?("HTTP "+r.http_status):"tidak terjangkau");h.className="pill "+(ok?"ok":"bad");}
$("recheck").onclick=health;

function render(r, rid){
 $("out").hidden=false;const b=r.body||{};const s=r.http_status;
 $("code").textContent=s?("HTTP "+s):"gagal";$("code").className="pill "+(s===200?"ok":s===202?"warn":"bad");
 $("meta").textContent=` · ${r.elapsed_ms} ms · request_id ${rid}`;
 const g=b.guardrails;$("summary").innerHTML=`<p>${esc(b.status_desc||"")} ${esc(b.message||"")}
  ${b.errors?`<br><span class="bad">errors: ${esc(b.errors)}</span>`:""}
  ${b.pipeline_last_stage?`<br>pipeline_last_stage: <b>${esc(b.pipeline_last_stage)}</b>`:""}
  ${g===0?'<br>guardrails: <span class="ok">lolos (0)</span>':g===1?'<br>guardrails: <span class="bad">ditolak (1)</span>':""}</p>`;
 const d=b.data;let html="";
 if(d&&Array.isArray(d.slip)){html+=`<p><b>${d.total_slip}</b> slip</p>`;
  d.slip.forEach((slip,i)=>{html+=`<h3>Slip ${i+1} · halaman ${esc(slip.page)}</h3><table><tr><th>Field</th><th>Nilai</th><th>confidence</th></tr>`;
   for(const [k,v] of Object.entries(slip)){if(v&&typeof v==="object"&&"value" in v){
    html+=`<tr><td>${esc(k)}</td><td>${esc(v.value??"—")}</td><td class="${v.confidence===1?"ok":"muted"}">${esc(v.confidence)}</td></tr>`;}}
   html+="</table>";if((slip.missing_mandatory_fields||[]).length)html+=`<p class="warn">Field wajib kosong: ${esc(slip.missing_mandatory_fields.join(", "))}</p>`;});}
 $("slips").innerHTML=html;$("json").textContent=JSON.stringify(b,null,2);}

$("f").onsubmit=async(e)=>{e.preventDefault();const fd=new FormData($("f"));
 const seq=[...document.querySelectorAll(".seq input:checked")].map(x=>x.value);
 if(seq.length&&seq.length<4)fd.set("pipeline_name_sequence",JSON.stringify(seq));
 $("send").disabled=true;$("state").textContent="mengirim…";
 try{const res=await fetch("/api/extract",{method:"POST",body:fd});const rid=res.headers.get("X-Request-Id");let r=await res.json();render(r,rid);
  const until=Date.now()+180000;
  while(r.http_status===202&&Date.now()<until){$("state").textContent="202: diproses di VM, menunggu hasil…";
   await new Promise(ok=>setTimeout(ok,2500));r=await (await fetch("/api/result/"+encodeURIComponent(rid))).json();render(r,rid);}
  $("state").textContent=r.http_status===202?"masih 202 setelah 3 menit: cek log VM":"selesai";}
 catch(err){$("state").textContent="galat: "+err;}finally{$("send").disabled=false;}};
loadConfig();health();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return PAGE


if __name__ == "__main__":
    print(f"VM tester: http://127.0.0.1:{PORT}  ->  {VM_BASE_URL}  (API key {'set' if VM_API_KEY else 'MISSING'})")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
