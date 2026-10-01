# Deploy ke GKE — runbook untuk tim deploy

Untuk tim yang men-deploy `nilam-ocr-slipgaji` ke GKE, tanpa akses ke laptop pengembang. Semua yang
dibutuhkan ada di repo ini; yang tidak ada di repo (rahasia, alamat cluster) ditandai **[isi]**.

## 1. Yang di-deploy

Satu Helm chart, `deploy/helm/nilam-ocr-slipgaji`, tujuh Deployment + Service:

| Deployment / Service | Port | Image | DB |
|---|---|---|---|
| `ms-bribrain-nilam-ocr-slipgaji-orchestrator` | 8034 | `ms-bribrain-nilam-ocr-slipgaji-orchestrator` | – |
| `ms-bribrain-nilam-ocr-slipgaji-extraction` | 8030 | `ms-bribrain-nilam-ocr-slipgaji-extraction` | ya |
| `ms-bribrain-nilam-ocr-slipgaji-guardrail-blank` | 8035 | `ms-bribrain-nilam-ocr-slipgaji-guardrail-blank` | – |
| `ms-bribrain-nilam-ocr-slipgaji-guardrail-blur` | 8036 | `ms-bribrain-nilam-ocr-slipgaji-guardrail-blur` | – |
| `ms-bribrain-nilam-ocr-slipgaji-guardrail-identity` | 8037 | `ms-bribrain-nilam-ocr-slipgaji-guardrail-identity` | – |
| `ms-bribrain-nilam-ocr-slipgaji-structuring` | 8032 | `ms-bribrain-nilam-ocr-slipgaji-structuring` | ya |
| `ms-bribrain-nilam-ocr-slipgaji-scoring` | 8033 | `ms-bribrain-nilam-ocr-slipgaji-scoring` | ya |

Plus Service gabungan `ms-bribrain-nilam-ocr-slipgaji` (hanya port 8034, orchestrator): alamat yang dipakai
Orkestrasi pusat. Semua model ada di dalam image (JSON + numpy); tidak ada berkas bobot terpisah.

## 2. Prasyarat

| Hal | Keterangan |
|---|---|
| Namespace | `nilam-ocr-slipgaji` **[isi bila lain]** |
| Registry | Artifact Registry untuk 7 image **[isi]** |
| PostgreSQL | Basis data yang dipakai bersama extraction/structuring/scoring, mis. `bribrain_ocr_nilam` **[isi]** |
| Layanan OCR | PaddleOCR (PP-OCRv6) HTTP, terjangkau dari namespace: `services.extraction.env.EXTRACTION_OCR_URL` **[isi]** |
| Orkestrasi pusat | URL callback + `X-Callback-Key`, atau tabel `ORCHESTRATION_API_EVENTS_TABLE` **[isi]** |
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
Tag image = SHA commit (tidak pernah `latest`), supaya selalu bisa dilacak dan dibangun ulang.

## 4. Secret

Satu Secret `nilam-ocr-slipgaji-secrets` (nama: `existingSecret`), dibuat manual atau lewat External Secrets
(`externalSecret.enabled: true`). Contoh: `deploy/gke/secret.example.yaml`.

| Key | Wajib | Dipakai |
|---|---|---|
| `API_KEY` | ya | semua service (dan header ke service lain) |
| `API_KEYS` | tidak | key tambahan saat rotasi |
| `DATABASE_URL` | ya | `postgresql+asyncpg://user:pass@host:5432/db` |
| `ORCHESTRATION_API_KEY` | tidak | callback format `stage` |
| `ORCHESTRATION_CALLBACK_KEY` | ya bila `orchestration.callbackFormat: result` | header `X-Callback-Key` |

## 5. Migrasi basis data (sebelum service)

```bash
kubectl -n nilam-ocr-slipgaji run migrate --rm -i --restart=Never \
  --image=$REGISTRY/ms-bribrain-nilam-ocr-slipgaji-migrate:$TAG \
  --env="DATABASE_URL=$DATABASE_URL" -- python -m alembic -c db/alembic.ini upgrade head
```

Atau `deploy/helm/migrate-db.sh`. Migrasi idempoten; jalankan setiap rilis. Tabel `prompts` (0010) kosong
sampai prompt dimasukkan (hanya perlu bila `PROMPT_SOURCE=db`).

## 6. Install / upgrade / rollback

```bash
helm upgrade --install nilam-ocr-slipgaji deploy/helm/nilam-ocr-slipgaji \
  -n nilam-ocr-slipgaji --create-namespace \
  -f deploy/helm/nilam-ocr-slipgaji/values-<dev|staging|production>.yaml \
  --set image.registry=$REGISTRY --set image.tag=$TAG --wait --timeout 10m

kubectl -n nilam-ocr-slipgaji get pods                 # 7 pod Running/Ready
helm -n nilam-ocr-slipgaji rollback nilam-ocr-slipgaji # kembali ke revisi sebelumnya
```

Cek sebelum apply: `helm lint` dan `helm template` (CI melakukannya), `python scripts/check_helm_values.py`.

## 7. Mematikan satu guardrail

```bash
helm upgrade ... --reuse-values --set services.guardrail-blur.enabled=false
```

Deployment guardrail itu dihapus dan extraction menerima `GUARDRAIL_BLUR_ENABLED=false`; dua guardrail lain
tetap menilai, laporan mencatat `skipped: [blur]`. Guardrail yang menyala tetapi tumbang juga tidak
menghentikan pipeline (`GUARDRAILS_FAIL_OPEN=true`), tercatat di `unavailable`.

## 8. Probe dan sumber daya

- `startupProbe` / `livenessProbe`: port TCP; `readinessProbe`: `GET /ready` (cek DB untuk tahap pipeline).
- `GET /health` untuk manusia: 503 `unhealthy` bila DB tidak terjangkau.
- Request/limit per service di `values.yaml` (`services.<nama>.resources`), HPA opsional
  (`services.<nama>.autoscaling`), PDB `maxUnavailable: 1`, NetworkPolicy default-deny (ingress orchestrator
  dari `networkPolicy.clientNamespaces`).

## 9. Smoke test setelah deploy

```bash
kubectl -n nilam-ocr-slipgaji port-forward svc/ms-bribrain-nilam-ocr-slipgaji 8034:8034 &
BASE_URL=http://127.0.0.1:8034 API_KEY=<API_KEY> python scripts/smoke_e2e.py <slip_gaji_asli.pdf>
```

Harus `0 failure(s)`. Bila gagal, kirim ke tim pengembang: keluaran skrip ini, `kubectl get pods`,
dan `kubectl logs deploy/<nama> --tail=200` dari service yang disebut `pipeline_last_stage`.
