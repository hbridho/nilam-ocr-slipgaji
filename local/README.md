# `local/` — menjalankan dan menguji di laptop

Hanya untuk laptop: tidak ada yang di sini yang ikut ke image, Helm, atau CI. Semua perintah dari akar repo.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
# sekali: salin services\<service>\.env.example ke .env (API_KEY sama di ketujuhnya)
.venv\Scripts\python local\run_local.py                    # nyalakan ketujuhnya, tunggu sehat
.venv\Scripts\python local\run_local.py --status
.venv\Scripts\python local\run_local.py --stop
.venv\Scripts\python local\run_local.py --port-offset 100  # stack kedua di 8130-8137
```

Log: `local/logs/<service>.log`. Tanpa akses ke layanan OCR, pakai `EXTRACTION_BACKEND=mock` di
`services/extraction/.env` (guardrail, structuring, dan scoring tetap memakai model asli).

| Service | Port | Swagger |
|---|---|---|
| orchestrator | 8034 | http://127.0.0.1:8034/docs |
| extraction | 8030 | http://127.0.0.1:8030/docs |
| guardrail-blank | 8035 | http://127.0.0.1:8035/docs |
| guardrail-blur | 8036 | http://127.0.0.1:8036/docs |
| guardrail-identity | 8037 | http://127.0.0.1:8037/docs |
| structuring | 8032 | http://127.0.0.1:8032/docs |
| scoring | 8033 | http://127.0.0.1:8033/docs |

## Menguji

```powershell
$env:BASE_URL="http://127.0.0.1:8034"; $env:API_KEY="<API_KEY>"
.venv\Scripts\python scripts\smoke_e2e.py                    # kontrak [07] lengkap lewat orchestrator
.venv\Scripts\python scripts\smoke_e2e.py D:\path\ke\slip.pdf  # dengan slip gaji asli (OCR asli)
.venv\Scripts\python local\coba_guardrail_3.py               # ketiga guardrail paralel + gabungan
```

Uji ketahanan guardrail: hentikan satu proses guardrail (atau set `GUARDRAIL_BLUR_ENABLED=false` di
`services/extraction/.env`), lalu jalankan smoke lagi — permintaan tetap 200, laporan guardrail
mencatat `unavailable` / `skipped`.
