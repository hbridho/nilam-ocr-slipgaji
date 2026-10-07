# Deploy ke VM dengan Docker: runbook

Target dev slip gaji: **VM GCE `gc-bribrain-dev-gce-facematch-01`**, dengan Docker (keputusan tim per dokumen,
6 Okt 2026). Tujuh container, image yang sama dengan GKE; chart Helm (`deploy/helm/`) tetap dijaga untuk kalau
slip gaji dipindah ke GKE.

| Container | Port | DB | Model dari GCS |
|---|---|---|---|
| `ms-bribrain-nilam-ocr-slipgaji-orchestrator` | **8034** (satu-satunya yang dibuka di VM) | – | – |
| `ms-bribrain-nilam-ocr-slipgaji-extraction` | 8030 | ya | – |
| `ms-bribrain-nilam-ocr-slipgaji-guardrail-blank` | 8035 | – | `unreadable_doc_confidence` |
| `ms-bribrain-nilam-ocr-slipgaji-guardrail-blur` | 8036 | – | `unreadable_doc_confidence` |
| `ms-bribrain-nilam-ocr-slipgaji-guardrail-identity` | 8037 | – | `is_slip_gaji_doc_confidence` |
| `ms-bribrain-nilam-ocr-slipgaji-structuring` | 8032 | ya (+ `system_prompt`) | – |
| `ms-bribrain-nilam-ocr-slipgaji-scoring` | 8033 | ya | `field_confidence` |
| `ms-bribrain-nilam-ocr-slipgaji-redis` (opsional, `COMPOSE_PROFILES=redis`) | 6379, hanya di jaringan compose | – | – |

## 1. Prasyarat di VM

- Docker Engine + Docker Compose **v2.24 atau lebih baru** (`docker compose version`).
- Pull dari Artifact Registry: `gcloud auth configure-docker asia-southeast2-docker.pkg.dev` (service account VM
  butuh `roles/artifactregistry.reader`). Tanpa akses registry: bangun di VM, lihat langkah 3b.
- Terjangkau dari VM: Cloud SQL **`10.213.224.113:3307`** (port connector, bukan 5432), `sqladmin.googleapis.com`,
  `sts.googleapis.com`, `iamcredentials.googleapis.com`, `login.microsoftonline.com`; layanan PaddleOCR; endpoint
  callback Orkestrasi; untuk model GCS `storage.googleapis.com`. Cek: `nc -vz 10.213.224.113 3307`.
- Hak database (DBA, sekali): user `CLOUDSQL_USER` butuh `CREATE` di database `nilam` (migrasi membuat skema
  `nilam_ocr_slipgaji`), lalu SELECT/INSERT/UPDATE/DELETE di skema itu.

## 2. Konfigurasi

```bash
cd deploy/vm
cp vm.env.example .env && chmod 600 .env    # isi yang bertanda [WAJIB] / [RAHASIA]; JANGAN commit
```

`vm.env.example` adalah **satu `.env` untuk seluruh alur** (handover): bagian 1-11 mengikuti urutan alur, dari
image, API key, orchestrator, OCR, guardrails, model GCS, structuring/LLM, scoring, Cloud SQL, hasil ke Orkestrasi,
sampai log/APM. Nilai dev yang diketahui sudah terisi; rahasia dikosongkan. `docker-compose.yml` membagikan tiap
variabel hanya ke container yang memakainya.

- `CLOUDSQL_*`: Cloud SQL `nilam`, seperti nilam-ocr-shm. Instance
  `edm-bribrain-dev-01:asia-southeast2:gc-bribrain-dev-sql-psql-01`, database `nilam`, `PRIVATE` sudah terisi;
  isi `CLOUDSQL_USER` / `CLOUDSQL_PASSWORD` (user database) dan aplikasi Entra untuk Cloud SQL
  (`CLOUDSQL_AZURE_*`, `CLOUDSQL_GCP_*`, isinya sama dengan `cloudsql.env` / `connect_cloudsql.py`). Semua tabel
  ada di skema `nilam_ocr_slipgaji`. Tidak ada `DATABASE_URL`.
- `ORCHESTRATION_URL` + `ORCHESTRATION_CALLBACK_KEY`: callback hasil. Belum ada endpoint yang terjangkau dari VM?
  Kosongkan URL dan set `ORCHESTRATION_CALLBACK_ENABLED=false`: Orkestrasi mem-poll `GET /v1/extract-ocr/{id}`.
- Model: tiga `*_MODEL_GCS_URI` sudah terisi ke bucket `gc-bribrain-dev-gcs-ocr-nilam-01/nilam-ocr-slipgaji/`.
  Kredensial: `GCS_AZURE_*`/`GCS_GCP_*` dari `wif.gcs.env`, atau kosongkan semuanya untuk memakai service account
  VM (butuh `roles/storage.objectViewer` di bucket). URI dikosongkan = model yang ikut di image.
- LLM: `ENABLE_LLM=true` + `LLM_BACKEND` (`bedrock` dengan `BEDROCK_*`, atau `http` dengan `LLM_ENDPOINT` /
  `LLM_GATEWAY_KEY`). Prompt dari tabel: `PROMPT_SOURCE=db`.
- Cache prompt di Redis (seperti nilam-ocr-shm, key `ocr:prompt:slipgaji`): `PROMPT_SOURCE=db`,
  `STRUCTURING_PROMPT_REDIS_ENABLED=true`, `COMPOSE_PROFILES=redis` dan `REDIS_PASSWORD` (`openssl rand -hex 24`).
  Container kedelapan `redis` ikut jalan; `STRUCTURING_REDIS_URL` kosong = container itu. Prompt baru lalu berlaku
  tanpa restart (lihat Operasi). Pakai Redis Orkestrasi: isi `STRUCTURING_REDIS_URL`, kosongkan `COMPOSE_PROFILES`.

## 3a. Rilis (image dari registry)

```bash
export TAG=<sha commit>        # atau tulis di .env
docker compose pull
docker compose --profile migrate run --rm migrate      # idempoten; jalankan setiap rilis
docker compose up -d
docker compose ps                                      # 7 container "healthy"
```

## 3b. Rilis (bangun di VM)

```bash
git checkout <sha> && cd deploy/vm
export REGISTRY=local TAG=$(git rev-parse --short HEAD)
docker compose -f docker-compose.yml -f docker-compose.build.yml build
docker compose --profile migrate run --rm migrate && docker compose up -d
```

## 4. Operasi

| Perlu | Perintah |
|---|---|
| Log satu service | `docker compose logs -f --tail=200 extraction` |
| Matikan satu guardrail | `.env`: `GUARDRAIL_BLUR_ENABLED=false`, lalu `docker compose up -d` (opsional `docker compose stop guardrail-blur`) |
| Model baru dari GCS | unggah versi baru, ganti URI di `.env`, `docker compose up -d` (container yang berubah dibuat ulang) |
| Prompt baru (`PROMPT_SOURCE=db`) | `bash deploy/vm/scripts/set_prompt.sh <berkas.md> "catatan"`: baris baru di `nilam_ocr_slipgaji.system_prompt` jadi `is_active`, lalu key Redis dihapus sehingga job berikutnya memakainya (tanpa Redis: `docker compose restart structuring`). Tanpa argumen = `services/structuring/prompts/slip_gaji.v1.md`; idempoten. Database baru dengan `PROMPT_SOURCE=db`: jalankan sekali setelah migrasi |
| Cek ujung ke ujung setelah deploy | `bash deploy/vm/scripts/probe.sh`: dua PDF sintetis (halaman kosong harus 400, slip karangan harus 200) lewat orchestrator, lalu 6 baris mereka di `nilam_guardrails_results`. Tanpa data nasabah |
| Hapus key prompt (Redis) | `docker exec ms-bribrain-nilam-ocr-slipgaji-redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli DEL ocr:prompt:slipgaji'`. Tanpa ini prompt baru berlaku paling lambat setelah `STRUCTURING_PROMPT_REDIS_TTL_SECONDS` (3600 dtk) |
| Lihat key prompt | `docker exec ms-bribrain-nilam-ocr-slipgaji-redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli GET ocr:prompt:slipgaji' \| head -c 300`; `TTL ocr:prompt:slipgaji` untuk sisa umurnya |
| Rollback | `TAG=<sha lama>` lalu `docker compose up -d`. Ke image sebelum migrasi 0010 (tabel masih di `public`): turunkan dulu, `docker compose run --rm migrate python -m alembic -c db/alembic.ini downgrade 0009_guardrails_results_sequence` dengan image yang baru |
| Berhenti | `docker compose down` (data ada di Cloud SQL, tidak ada volume; Redis hanya cache, diisi lagi dari tabel) |

Container berjalan read-only dengan `/tmp` tmpfs, `restart: unless-stopped` (ikut menyala setelah VM reboot),
log json-file dibatasi 5 × 50 MB per container.

## 5. Smoke test

```bash
BASE_URL=http://127.0.0.1:8034 API_KEY=<API_KEY> python scripts/smoke_e2e.py <slip_gaji_asli.pdf>
```

Harus `0 failure(s)`. Bila gagal, kirim: keluaran skrip, `docker compose ps`, dan
`docker compose logs --tail=200 <service>` dari service yang disebut `pipeline_last_stage`.
