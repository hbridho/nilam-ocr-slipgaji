# Database

Satu database PostgreSQL dipakai bersama oleh repo ini, dokumen NILAM lain, **dan** service orkestrasi: Cloud SQL
`nilam` (`edm-bribrain-dev-01:asia-southeast2:gc-bribrain-dev-sql-psql-01`). Tiap dokumen punya skemanya sendiri;
semua tabel repo ini ada di skema **`nilam_ocr_slipgaji`** dengan awalan `nilam_` (migrasi `0010`), seperti
`nilam_ocr_shm` milik SHM. Nama di tabel di bawah tanpa awalan itu. Karena itu penting jelas: tabel mana milik siapa.

Koneksi: `CLOUDSQL_*` (Cloud SQL Python Connector, aplikasi Entra → WIF → service account; port 3307, lihat
`libs/ocr_common/ocr_common/pipeline/cloudsql.py`), atau `DATABASE_URL` untuk Postgres lokal dan tes. Bukan keduanya.

## Peta tabel

| Tabel | Pemilik schema | Ditulis | Dibaca | Isi |
|---|---|---|---|---|
| `ocr_jobs`, `ocr_results` | **repo ini** | extraction | extraction (`GET /v1/extraction/jobs/{request_id}`), orchestrator lewat API itu | status dan hasil tahap OCR |
| `structuring_jobs`, `structuring_results` | **repo ini** | structuring | structuring lewat API-nya (orchestrator) | status dan field hasil structuring |
| `scoring_jobs`, `scoring_results` | **repo ini** | scoring | scoring lewat API-nya (orchestrator) | status dan skor trust model |
| `guardrails_results` | **repo ini** | extraction (job OCR, best-effort) | tidak ada service; untuk audit dan analisis | jawaban **tiap guardrail**: satu baris per `guardrail` (`blank`, `blur`, `identity`) setiap kali dokumen dinilai, termasuk dokumen yang ditolak; guardrail yang dimatikan/tidak menjawab tetap dapat baris (`verdict` `skipped`/`unavailable`). `confidence` dan `threshold` di sisi accept (`passed` = `confidence >= threshold`), asal threshold (`threshold_source`: `request` dari Orkestrasi pusat, `service` milik guardrail), dan jawaban guardrail apa adanya di `report`. Append-only (migrasi `0008`, kolom `guardrail` migrasi `0011`) |
| `ocr_slip_gaji_requests` | — | tidak ada | tidak ada | tabel kontrak lama sinkron (`generate-request-id` → `extract-ocr` → `get-ocr-result`) yang sudah dihapus dari extraction; **dihapus oleh migrasi `0007_drop_ocr_slip_gaji_requests`**. Jumlah barisnya dicatat di log job migrasi sebelum di-drop; `downgrade` membuat ulang tabel kosong, isinya tidak kembali |
| `pipeline_outbox` | **repo ini** | ketiga tahap (dalam transaksi job), relay | relay tiap service, `GET /v1/<tahap>/outbox` | callback dan handoff yang belum terkirim (`PIPELINE_OUTBOX`). Baris dihapus setelah terkirim; yang gagal permanen (4xx, atau 5xx lebih lama dari `PIPELINE_OUTBOX_MAX_AGE_SECONDS`) tetap ada sebagai dead letter dengan `failed_at` + `last_error`, tidak pernah diambil lagi oleh relay, dan dilepas manual dengan `failed_at = NULL, next_attempt_at = now()`. `ds` dipakai untuk membersihkan dead letter lama |
| `testing_ocr_jobs`/`_results`, `testing_structuring_jobs`/`_results`, `testing_scoring_jobs`/`_results`, `testing_pipeline_outbox`, `testing_guardrails_results` | **repo ini** | ketiga tahap (dan orchestrator untuk putusan guardrails) lewat endpoint `-test` (`TESTING_ENDPOINTS`) | tahap itu sendiri, orchestrator lewat `GET /v1/<tahap>/jobs-test/{request_id}` | salinan persis tabel tahap dan outbox untuk load test tim ML (migrasi `0006`). Tidak pernah dibaca Orkestrasi; boleh di-`TRUNCATE` kapan saja setelah tes. Lihat README, "Endpoint Testing" |
| `ocr_slip_gaji_alembic_version` | **repo ini** | Alembic | Alembic | versi migrasi repo ini; namanya sengaja tidak `alembic_version` supaya tidak bentrok dengan migrasi tim lain |
| `ocr.orchestration_api_events` | **orkestrasi** | orkestrasi; ketiga tahap menambah baris keadaan akhir kalau `ORCHESTRATION_API_EVENTS_TABLE` diisi | orkestrasi | log API orkestrasi, append-only. Lihat bagian di bawah tabel ini |
| `orchestration_*` lainnya, `auth_*`, `datahub_lookup_log` (schema `ocr`) | **orkestrasi** | orkestrasi | orkestrasi | di luar repo ini. Migrasi di sini tidak pernah membuat atau mengubahnya |
| `ocr.*`, `structuring.*`, `scoring.*` (schema terpisah) | — | tidak ada | tidak ada | sisa desain lama sebelum tabel pindah ke schema `public`; **dihapus oleh migrasi `0005_drop_legacy_schemas`**. Migrasi itu hanya membuang schema yang isinya persis `jobs` + `results`; kalau ada tabel atau view lain di dalamnya, migrasi berhenti dengan pesan supaya diperiksa dulu. Jumlah baris yang dibuang dicatat di log Alembic |

Orchestrator membaca status tahap lewat API dan tidak menulis database. Guardrails tidak punya tabel: ketiganya dipanggil extraction setelah OCR, dan extraction yang mencatat jawabannya di `guardrails_results`.

Contoh analisis, berapa dokumen yang ditahan tiap guardrail per hari:

```sql
SELECT ds, guardrail, verdict, count(*)
  FROM nilam_ocr_slipgaji.nilam_guardrails_results
 GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2, 3;
```

Ketiga tahap juga bisa menulis status request ke `orchestration_extract_ocr` di transaksi
yang sama dengan penyimpanan hasilnya, kalau `ORCHESTRATION_OUTCOME_TABLE` diisi (default
mati). Yang ditulis: `processing` + tahapnya saat job diklaim, `completed` + `result_data`
oleh scoring, dan `failed` + `error_code` saat gagal. **Kontraknya adalah kolom `downstream_status`**:
Orkestrasi hanya membaca kolom itu (`processing` | `completed` | `failed`) untuk menjawab polling
client; kolom lain (`downstream_stage`, `status_code`, `error_code`, `error_message`, `result_data`)
adalah data pendamping. Request yang ditolak guardrails (400) tidak pernah menulis kolom ini,
karena tidak ada tahap yang berjalan; Orkestrasi menjawab client dari respons sinkron itu. Tabel itu **milik orkestrasi**, jadi
kolomnya mereka yang menambahkan; DDL yang dibutuhkan (termasuk `request_id` unik) ada di
[external/orchestration_extract_ocr.sql](external/orchestration_extract_ocr.sql) dan bisa
dipasang ke PostgreSQL lokal dengan `make db-external`.

Orkestrasi di dev membaca hasil dari log API-nya, `ocr.orchestration_api_events`. Kalau
`ORCHESTRATION_API_EVENTS_TABLE=ocr.orchestration_api_events` diisi, tiap tahap **menambah** satu
baris di transaksi yang sama dengan tabel job-nya sendiri (double write), hanya untuk keadaan akhir:

| Keadaan | `endpoint` | `status_code` | `downstream_status` | `downstream_stage` | `error_code` |
|---|---|---|---|---|---|
| selesai (scoring) | `GET_OCR_RESULT` | 200 | `COMPLETED` | `SCORING` | kosong |
| gagal di satu tahap | `GET_OCR_RESULT` | 422 | `FAILED` | `EXTRACTION`, `STRUCTURING`, atau `SCORING` | `OCR_FAILED`, `STRUCTURING_FAILED`, `SCORING_FAILED` |
| ditolak aturan structuring | `GET_OCR_RESULT` | 400 | `FAILED` | `STRUCTURING` | `DOWNSTREAM_VALIDATION_ERROR` |

`result_data` mengikuti bentuk baris polling orkestrasi sendiri: `{result, status, document_type,
error_code, error_message, created_at, updated_at}`. `result` berisi data kontrak `extract-ocr`
(`nomor_slip_gaji`, `nama`) plus `document_type` dan `guardrails`, dan kosong kalau gagal atau ditolak.
Untuk penolakan, `error_message` adalah alasan dari aturan ML (bahasa Indonesia). Tabel ini tidak punya kunci unik per `request_id`, jadi request yang dijalankan ulang
mendapat baris baru; **baris terbaru per `request_id` adalah keadaannya**. Kalau tabel ini gagal
ditulis, penulisan job ikut dibatalkan. DDL tiruannya ada di
[external/orchestration_api_events.sql](external/orchestration_api_events.sql).

## Sumber kebenaran

Kolom didefinisikan **sekali** di [`libs/ocr_common/ocr_common/pipeline/tables.py`](../libs/ocr_common/ocr_common/pipeline/tables.py).
Migrasi Alembic dan `create_all` di test memakai definisi yang sama, dan `make db-check`
gagal kalau keduanya menyimpang.

## Perintah

```bash
export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/nilam   # Postgres lokal
# atau Cloud SQL: export CLOUDSQL_INSTANCE=... CLOUDSQL_DATABASE=nilam dan CLOUDSQL_* lainnya (deploy/vm/vm.env.example)

make db-upgrade                  # jalankan migrasi sampai revisi terakhir
make db-check                    # gagal kalau definisi tabel di kode beda dengan database
make db-revision m="tambah kolom X"   # buat revisi baru dari selisihnya, lalu PERIKSA hasilnya
```

Database yang tabelnya sudah ada (mis. dev yang dulu dipasang manual) cukup dijalankan
`make db-upgrade`: revisi baseline memakai `CREATE TABLE IF NOT EXISTS`, jadi tabel dan
datanya dibiarkan, dan database itu tercatat berada di revisi baseline.

## Deploy

Cloud SQL `nilam` hanya punya IP private (laptop tidak menjangkaunya), jadi migrasi jalan dari mesin di VPC:

```bash
docker compose --profile migrate run --rm migrate          # VM (deploy/vm), CLOUDSQL_* dari deploy/vm/.env
deploy/helm/db-job.sh alembic upgrade head                 # GKE: Job, CLOUDSQL_* dari Secret
```

**Jalankan sebelum** men-deploy image yang membutuhkan perubahan tabelnya. Untuk Postgres biasa yang terjangkau:
`DB_HOST=<alamat postgres> deploy/helm/migrate-db.sh` (mengambil `DATABASE_URL` dari Secret).

Untuk PostgreSQL lokal, `make up-db` menjalankan migrasi lebih dulu lewat service
`migrate` di `docker-compose.db.yml`; service lain baru start setelah migrasi selesai.
