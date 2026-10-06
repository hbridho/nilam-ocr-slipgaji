# Helm chart `nilam-ocr-slipgaji`

Tujuh Deployment + Service `ms-bribrain-nilam-ocr-slipgaji-<service>` dan Service gabungan
`ms-bribrain-nilam-ocr-slipgaji` (orchestrator, 8034). Dev slip gaji berjalan di VM dengan Docker
([../vm/README.md](../vm/README.md)); chart ini dijaga setara nilam-ocr-npwp untuk GKE.

- Runbook (prasyarat, rahasia, migrasi, install/upgrade/rollback, smoke test): [../DEPLOY.md](../DEPLOY.md)
- Nilai per lingkungan: `nilam-ocr-slipgaji/values-ddb-dev.yaml`, `values-staging.yaml`, `values-production.yaml`
- Mematikan satu guardrail: `--set services.guardrail-blur.enabled=false`
- `deploy.sh`: build + push + `helm upgrade` dari satu commit bersih (`DEPLOY_ENV=ddb-dev|staging|production`)
- `migrate-db.sh` / `db-job.sh`: migrasi `db/` lewat image `db/Dockerfile`
- Pemeriksaan sebelum apply: `helm lint`, `helm template`, `python scripts/check_helm_values.py`

## Cloud SQL

Seperti nilam-ocr-shm: Cloud SQL `nilam` (`CLOUDSQL_INSTANCE`, `CLOUDSQL_DATABASE`, `CLOUDSQL_IP_TYPE` di `commonEnv`
`values-ddb-dev.yaml`), kredensial `CLOUDSQL_*` di Secret (`cloudsqlSecretKeys`), masuk ke extraction, structuring,
scoring. Login database `CLOUDSQL_USER` + `CLOUDSQL_PASSWORD` (dev: `bribrain_user`); hapus keduanya dari Secret untuk
login IAM sebagai service account setelah DBA mengaktifkan `cloudsql.iam_authentication`. Google API dipanggil lewat
aplikasi Entra untuk Cloud SQL (`CLOUDSQL_AZURE_*` → WIF → `gc-bribrain-dev-sac-sql-01`), tanpa GKE Workload Identity.
Jangan isi `DATABASE_URL` bersamaan: service menolak start.

1. Pod harus menjangkau `10.213.224.113:3307`. User database butuh `CREATE` di `nilam` (sekali).
2. Secret: `CLOUDSQL_*` (lihat `deploy/gke/secret.example.yaml`).
3. Migrasi: `./db-job.sh alembic upgrade head` (Job membaca `CLOUDSQL_*` dari Secret).
4. `./deploy.sh` / `helm upgrade`.

Memindahkan isi database lama: `SOURCE_DATABASE_URL=... TARGET_DATABASE_URL=... python db/copy_database.py`
(lihat `db/README.md`).

## Model dari GCS

1. Unggah `dist/gcs/nilam-ocr-slipgaji/{guardrails,scoring}` (`python scripts/export_gcs_models.py`) ke
   `gs://gc-bribrain-dev-gcs-ocr-nilam-01/nilam-ocr-slipgaji/`.
2. Secret: `GCS_AZURE_CLIENT_SECRET` dari `wif.gcs.env`; values: blok `gcpWif` (contoh berkomentar di
   `values-ddb-dev.yaml`) dan `services.<nama>.env.<SERVICE>_MODEL_GCS_URI` untuk guardrail-blank, -blur,
   -identity dan scoring (`gcsModels: true`).
3. Pastikan pod menjangkau `login.microsoftonline.com` dan `*.googleapis.com`. Unduhan gagal = pod tidak start,
   jadi rollout yang salah berhenti di pod pertama.
