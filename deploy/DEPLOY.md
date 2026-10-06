# Deploy: runbook untuk tim deploy

Untuk tim yang men-deploy `nilam-ocr-slipgaji`, tanpa akses ke laptop pengembang. Yang tidak ada di repo
(rahasia, alamat) ditandai **[isi]**.

**Target per dokumen (keputusan tim, 6 Okt 2026): slip gaji di VM GCE `gc-bribrain-dev-gce-facematch-01` dengan
Docker** → runbook [vm/README.md](vm/README.md). Image, Secret/env, migrasi, GCS dan Cloud SQL di bawah berlaku
untuk keduanya; Helm (bagian 6-7) untuk kalau slip gaji dipasang di GKE seperti nilam-ocr-npwp.

## 1. Yang di-deploy

Tujuh service, satu image per service (+ image migrasi):

| Deployment / container | Port | DB | Model dari GCS |
|---|---|---|---|
| `ms-bribrain-nilam-ocr-slipgaji-orchestrator` | 8034 | – | – |
| `ms-bribrain-nilam-ocr-slipgaji-extraction` | 8030 | ya | – |
| `ms-bribrain-nilam-ocr-slipgaji-guardrail-blank` | 8035 | – | `BLANK_MODEL_GCS_URI` |
| `ms-bribrain-nilam-ocr-slipgaji-guardrail-blur` | 8036 | – | `BLUR_MODEL_GCS_URI` |
| `ms-bribrain-nilam-ocr-slipgaji-guardrail-identity` | 8037 | – | `IDENTITY_MODEL_GCS_URI` |
| `ms-bribrain-nilam-ocr-slipgaji-structuring` | 8032 | ya | – |
| `ms-bribrain-nilam-ocr-slipgaji-scoring` | 8033 | ya | `SCORING_MODEL_GCS_URI` |

Hanya orchestrator (8034) yang dipanggil Orkestrasi pusat. Model ikut di image (JSON + numpy); `*_MODEL_GCS_URI`
membuat service mengunduh versi di GCS saat start sebagai gantinya.

## 2. Prasyarat

| Hal | Keterangan |
|---|---|
| Registry | Artifact Registry untuk 8 image **[isi]** |
| Basis data | Cloud SQL `edm-bribrain-dev-01:asia-southeast2:gc-bribrain-dev-sql-psql-01`, database **`nilam`** (dipakai bersama dokumen NILAM lain, satu skema per dokumen); skema `nilam_ocr_slipgaji` dibuat migrasi. Terjangkau di `10.213.224.113:3307`; user butuh `CREATE` di `nilam` sekali |
| Layanan OCR | PaddleOCR (PP-OCRv6) HTTP, terjangkau: `EXTRACTION_OCR_URL` **[isi]** |
| Orkestrasi pusat | URL callback + `X-Callback-Key` **[isi]**, atau mode poll (`ORCHESTRATION_CALLBACK_ENABLED=false`) |
| Model GCS (opsional) | Bucket `gc-bribrain-dev-gcs-ocr-nilam-01`, folder `nilam-ocr-slipgaji/` (unggah dari `dist/gcs/`, lihat bagian 8) |
| LLM (opsional) | Mati secara bawaan (`ENABLE_LLM=false`). Bila dinyalakan: `LLM_BACKEND`, `LLM_ENDPOINT`, kredensialnya |

## 3. Image

Dibangun dari **akar repo** (Dockerfile menyalin `libs/`), satu per service:

```bash
TAG=$(git rev-parse --short HEAD)
for s in orchestrator extraction guardrail-blank guardrail-blur guardrail-identity structuring scoring; do
  docker build -f services/$s/Dockerfile -t $REGISTRY/ms-bribrain-nilam-ocr-slipgaji-$s:$TAG .
  docker push $REGISTRY/ms-bribrain-nilam-ocr-slipgaji-$s:$TAG
done
docker build -f db/Dockerfile -t $REGISTRY/ms-bribrain-nilam-ocr-slipgaji-migrate:$TAG .   # migrasi
```

Atau Cloud Build: `gcloud builds submit --config deploy/gke/cloudbuild.yaml --substitutions=_TAG=$TAG,_REGISTRY=$REGISTRY`.
Tag image = SHA commit (tidak pernah `latest`).

## 4. Rahasia

VM: `deploy/vm/.env` (dari `vm.env.example`). GKE: Secret `nilam-ocr-slipgaji-secrets` (`existingSecret`), manual
atau External Secrets; contoh `deploy/gke/secret.example.yaml`.

| Key | Wajib | Dipakai |
|---|---|---|
| `API_KEY` | ya | semua service (dan header ke service lain) |
| `API_KEYS` | tidak | key tambahan saat rotasi |
| `CLOUDSQL_USER` / `CLOUDSQL_PASSWORD` | ya (kosong keduanya = login IAM) | extraction, structuring, scoring: user database Cloud SQL `nilam` |
| `CLOUDSQL_AZURE_*`, `CLOUDSQL_GCP_*` | ya | aplikasi Entra untuk Cloud SQL → WIF → `gc-bribrain-dev-sac-sql-01` (seperti nilam-ocr-shm); `CLOUDSQL_INSTANCE` / `_DATABASE` / `_IP_TYPE` bukan rahasia (values / `.env`) |
| `DATABASE_URL` | hanya tanpa Cloud SQL | Postgres biasa: `postgresql+asyncpg://user:pass@host:5432/db`. Bersama `CLOUDSQL_INSTANCE` = service menolak start |
| `ORCHESTRATION_CALLBACK_KEY` | ya bila callback `result` menyala | header `X-Callback-Key` |
| `ORCHESTRATION_API_KEY` | tidak | callback format `stage` |
| `GCS_AZURE_CLIENT_SECRET` | bila model dari GCS lewat WIF Entra | identitas GCS (`wif.gcs.env` Tim SEA), masuk ke container sebagai `AZURE_CLIENT_SECRET`; di VM boleh diganti service account VM |
| `ELASTIC_APM_SECRET_TOKEN` / `ELASTIC_APM_API_KEY` | tidak | bila APM dinyalakan |

## 5. Migrasi basis data (sebelum service, setiap rilis)

```bash
docker run --rm --env-file deploy/vm/.env $REGISTRY/ms-bribrain-nilam-ocr-slipgaji-migrate:$TAG   # CLOUDSQL_*
```

(VM: `docker compose --profile migrate run --rm migrate`; GKE: `deploy/helm/migrate-db.sh` atau `db-job.sh`.)
Idempoten. Migrasi **0010** membuat skema `nilam_ocr_slipgaji`, memindahkan tabel lama dari `public` ke sana
dengan nama `nilam_*` **beserta isinya**, dan membuat tabel `system_prompt` (DDL tim). Pindah database (mis. ke
Cloud SQL): `db/copy_database.py`, lihat [../db/README.md](../db/README.md).

## 6. Helm (GKE): install / upgrade / rollback

```bash
helm upgrade --install nilam-ocr-slipgaji deploy/helm/nilam-ocr-slipgaji \
  -n nilam-ocr-slipgaji --create-namespace \
  -f deploy/helm/nilam-ocr-slipgaji/values-<ddb-dev|staging|production>.yaml \
  --set image.registry=$REGISTRY --set image.tag=$TAG --wait --timeout 10m

kubectl -n nilam-ocr-slipgaji get pods                 # 7 pod Running/Ready
helm -n nilam-ocr-slipgaji rollback nilam-ocr-slipgaji # kembali ke revisi sebelumnya
```

Nilai baru (sama dengan NPWP): `orchestration.callbackEnabled` / `callbackMaxAgeSeconds`, `apm.serverUrl`,
`gcpWif.*` (identitas GCS; client secret dari Secret) dan `services.<nama>.env.<SERVICE>_MODEL_GCS_URI`.
Cek sebelum apply: `helm lint`, `helm template` (CI melakukannya), `python scripts/check_helm_values.py`.

## 7. Mematikan satu guardrail

Helm: `--reuse-values --set services.guardrail-blur.enabled=false`. VM: `GUARDRAIL_BLUR_ENABLED=false` di `.env`,
lalu `docker compose up -d`. Extraction tidak memanggilnya lagi; dua guardrail lain tetap menilai, laporan
mencatat `skipped: [blur]`. Guardrail yang menyala tetapi tumbang juga tidak menghentikan pipeline
(`GUARDRAILS_FAIL_OPEN=true`), tercatat di `unavailable`.

## 8. Model di GCS

```bash
python scripts/export_gcs_models.py      # -> dist/gcs/nilam-ocr-slipgaji/{guardrails,scoring}/...
```

Unggah folder `guardrails` dan `scoring` ke `gs://gc-bribrain-dev-gcs-ocr-nilam-01/nilam-ocr-slipgaji/` (console:
Upload folder), lalu isi URI-nya (ada di `manifest.json` tiap model, juga SHA-256 untuk `<SERVICE>_MODEL_SHA256`).
Model baru = folder `v2/` di sebelah `v1/` (`--version 2`), ganti URI, restart. Unduhan yang gagal atau tidak cocok
membuat service menolak start; tanpa URI service memakai model di image.

## 9. Probe, log, APM

- `GET /ready` (cek DB untuk tahap pipeline), `GET /health` (503 `unhealthy` bila DB tidak terjangkau), `/metrics`.
- Elastic APM bila `ELASTIC_APM_SERVER_URL` diisi; nama service `ms-bribrain-nilam-ocr-slipgaji-<service>`.
  Isi body dan header request tidak pernah dikirim.

## 10. Smoke test setelah deploy

```bash
BASE_URL=http://<host>:8034 API_KEY=<API_KEY> python scripts/smoke_e2e.py <slip_gaji_asli.pdf>
```

Harus `0 failure(s)`. Bila gagal, kirim ke tim pengembang: keluaran skrip ini, status container/pod, dan log
200 baris terakhir dari service yang disebut `pipeline_last_stage`.
