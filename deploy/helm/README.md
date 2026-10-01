# Helm chart `nilam-ocr-slipgaji`

Tujuh Deployment + Service `ms-bribrain-nilam-ocr-slipgaji-<service>` dan Service gabungan
`ms-bribrain-nilam-ocr-slipgaji` (orchestrator, 8034).

- Runbook lengkap (prasyarat, Secret, migrasi, install/upgrade/rollback, smoke test): [../DEPLOY.md](../DEPLOY.md)
- Nilai per lingkungan: `nilam-ocr-slipgaji/values-dev.yaml`, `values-staging.yaml`, `values-production.yaml`
- Mematikan satu guardrail: `--set services.guardrail-blur.enabled=false`
- `deploy.sh`: build + push + `helm upgrade` dari satu commit bersih (`DEPLOY_ENV=dev|staging|production`)
- `migrate-db.sh`: migrasi `db/` lewat image `db/Dockerfile`
- Pemeriksaan sebelum apply: `helm lint`, `helm template`, `python scripts/check_helm_values.py`