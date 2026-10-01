# Repo Bitbucket per service (code review NCM)

Satu repo **per service**, bukan per dokumen. Isi tiap repo slip gaji dibuat dari monorepo ini dengan
`python scripts/split_services.py` → `dist/bitbucket/<nama-repo>/` (berdiri sendiri: kode service,
`libs/` yang dipakainya, Dockerfile, lock file, tes, `.env.example`, `openapi.yaml`).

| No | Dokumen | Service |
|---|---|---|
| 1 | KTP | ms-bribrain-nilam-ocr-ktp-orchestrator |
|  |  | ms-bribrain-nilam-ocr-ktp-laminate |
|  |  | ms-bribrain-nilam-ocr-ktp-recapture |
|  |  | ms-bribrain-nilam-ocr-ktp-graycopy |
|  |  | ms-bribrain-nilam-ocr-ktp-tamper |
|  |  | ms-bribrain-nilam-ocr-ktp-detector |
|  |  | ms-bribrain-nilam-ocr-ktp-iqa-rulebased |
|  |  | ms-bribrain-nilam-ocr-ktp-postprocessor |
|  |  | ms-bribrain-nilam-ocr-ktp-iqa-dl |
| 2 | SK Kerja | ms-bribrain-nilam-ocr-skkerja-orchestrator |
|  |  | ms-bribrain-nilam-ocr-skkerja-extraction |
|  |  | ms-bribrain-nilam-ocr-skkerja-guardrails |
|  |  | ms-bribrain-nilam-ocr-skkerja-structuring |
|  |  | ms-bribrain-nilam-ocr-skkerja-scoring |
| 3 | Slip Gaji | ms-bribrain-nilam-ocr-slipgaji-orchestrator |
|  |  | ms-bribrain-nilam-ocr-slipgaji-extraction |
|  |  | ms-bribrain-nilam-ocr-slipgaji-guardrail-blank |
|  |  | ms-bribrain-nilam-ocr-slipgaji-guardrail-blur |
|  |  | ms-bribrain-nilam-ocr-slipgaji-guardrail-identity |
|  |  | ms-bribrain-nilam-ocr-slipgaji-structuring |
|  |  | ms-bribrain-nilam-ocr-slipgaji-scoring |

**Slip gaji punya tiga repo guardrail, bukan satu.** Ketiga pemeriksaan (kosong, buram, bukan slip gaji)
adalah service terpisah supaya satu bisa dimatikan atau tumbang tanpa menjatuhkan yang lain; extraction
memanggil ketiganya bersamaan dan menggabungkan jawabannya.

Nama yang sama dipakai di seluruh repo: nama image Docker, Deployment/Service Kubernetes
(`fullnameOverride` di Helm), dan nama container di `docker-compose.yml`.

Migrasi basis data (`db/`) tetap di monorepo dan dijalankan sekali sebelum service (lihat
[deploy/DEPLOY.md](deploy/DEPLOY.md)); belum dibuat repo sendiri.
