# API Spec — OCR Slip Gaji (NILAM)

Untuk tim Orkestrasi pusat. Mengikuti format API spec **[07] OCR NPWP**: envelope, `pipeline_name_sequence`,
threshold 0–1, kode error, dan callback yang sama. Bedanya hanya di isi `data` (slip gaji, bisa beberapa
slip per berkas) dan di tempat guardrail berjalan (setelah OCR).

**Base URL**. Dev: VM `gc-bribrain-dev-gce-facematch-01` dengan Docker (`deploy/vm/`):

    http://<IP internal gc-bribrain-dev-gce-facematch-01>:8034

Bila dipasang di GKE (`deploy/helm/`): `http://ms-bribrain-nilam-ocr-slipgaji.nilam-ocr-slipgaji.svc.cluster.local:8034`.

Hanya orchestrator yang dipanggil. Semua endpoint kecuali `/health`, `/ready`, `/metrics` mewajibkan
`X-API-Key`. Dokumen: JPEG/PNG/PDF, maks. **2,5 MB**, maks. **12 halaman** (`MAX_DOCUMENT_PAGES`; berkas tiga
bulan lazim). Hasil dijawab langsung (200) bila selesai dalam **15 detik**, selain itu 202 lalu callback.

## 1. Health

`GET /health` → 200 `{"status": "healthy", "version": "1.0.0", "detail": null, "device": "cpu", "backends": {}}`,
atau 503 `unhealthy` dengan penyebab di `detail`.

## 2. Extract OCR — `POST /v1/extract-ocr` (multipart/form-data)

| Parameter | Wajib | Keterangan |
|---|---|---|
| `request_id` | Ya | Dari Orkestrasi pusat; request_id yang sama tidak menjalankan pipeline dua kali |
| `document_type` | Tidak | Bawaan `slip_gaji`; selain itu 400 `UNSUPPORTED_DOCUMENT_TYPE` |
| `file` / `file_url` | Salah satu | `file_url` harus berlaku > 5 menit (bisa diunduh ulang saat job diulang) |
| `pipeline_name_sequence` | Tidak | JSON array (atau field berulang). Tidak dikirim: keempatnya |
| `guardrails_confidence_threshold` | Tidak | JSON object per guardrail, sisi accept, 0 < x < 1: `{"acc_rej": 0.8}` (`acc_rej` = `identity`, kunci NPWP), `{"identity": 0.8, "blur": 0.7}`. Lolos bila P(accept) ≥ x (blur: 1 − P(rusak)). Angka polos, kunci lain, atau nilai di luar (0, 1) → 422 `INVALID_THRESHOLD`. Tidak dikirim: ambang tiap guardrail |
| `column_confidence_threshold` | Tidak | JSON per field, sisi accept, mis. `{"gaji_bersih": 0.9, "all_field": 0.6}`. `all_field` diterapkan ke ke-20 field, kunci field sendiri didahulukan; tidak disebut: 0,5 |

`params` dan `guardrails_tendency` (versi lama) diterima tetapi diabaikan, sama dengan NPWP.

```bash
curl -X POST "$BASE/v1/extract-ocr" -H "X-API-Key: <API_KEY>" \
  -F request_id=OCR_9cb01af2-493d-446d-b191-af120333f6d0 \
  -F file=@slip.pdf;type=application/pdf \
  -F 'pipeline_name_sequence=["guardrails","extraction","structuring","scoring"]' \
  -F 'guardrails_confidence_threshold={"acc_rej":0.5}' \
  -F 'column_confidence_threshold={"all_field":0.5}'
```

**200 — selesai**

```json
{
  "status_code": 200, "status_desc": "OK", "message": "OCR extraction completed successfully",
  "data": {
    "total_slip": 1,
    "slip": [{
      "page": 1,
      "nama_perusahaan": {"value": "PT SUMBER REJEKI MAKMUR", "confidence": 1},
      "periode": {"value": "2025-02", "confidence": 1},
      "nama_karyawan": {"value": "ANDI SAPUTRA", "confidence": 1},
      "gaji_pokok": {"value": 4500000, "confidence": 1},
      "bonus": {"value": null, "confidence": 0},
      "gaji_bersih": {"value": 4875000, "confidence": 1},
      "missing_mandatory_fields": []
    }]
  },
  "errors": null, "request_id": "OCR_9cb01af2-...",
  "pipeline_last_stage": null, "guardrails": 0
}
```

(Contoh dipendekkan: tiap slip selalu memuat ke-20 field.)

| Field `data` | Keterangan |
|---|---|
| `total_slip`, `slip[]` | Satu entri per halaman/slip, urut halaman |
| 20 field | `nama_perusahaan`, `periode` (YYYY-MM), `nama_karyawan`, `nomor_induk_karyawan`, `jabatan`, `divisi`, `status_pegawai`, `gaji_pokok`, `tunjangan_jabatan`, `tunjangan_transport`, `tunjangan_makan`, `tunjangan_komunikasi`, `tunjangan_lain`, `bonus`, `insentif`, `lembur`, `thr`, `total_pendapatan`, `total_potongan`, `gaji_bersih`. Uang: integer rupiah |
| `confidence` | 1 bila P(nilai benar) model keyakinan ≥ threshold field itu; 0 bila lebih rendah atau tidak ada nilai |
| `missing_mandatory_fields` | Field wajib yang kosong: `nama_perusahaan`, `nama_karyawan`, `periode`, `gaji_pokok`, `total_pendapatan`, `gaji_bersih` |

| Field envelope | Keterangan |
|---|---|
| `guardrails` | 1 = ditolak (guardrail atau aturan structuring), 0 = tidak, null = masih diproses / error sebelum dinilai |
| `pipeline_last_stage` | null pada 200 dan 202. Pada error: service yang gagal / menolak (`guardrails`, `extraction`, `structuring`, `scoring`), atau `orchestrator` untuk penolakan di pintu masuk |

## 3. Pipeline Name Sequence

Urutan `guardrails → extraction → structuring → scoring`. `guardrails` boleh dilewati dari depan, ekor boleh
dipotong, tengah tidak boleh dilewati; pelanggaran → 422 `INVALID_PIPELINE_SEQUENCE`, tidak ada yang jalan.

| Sequence | Isi `data` saat selesai |
|---|---|
| (tidak dikirim) / keempatnya | `{total_slip, slip[]}` dengan confidence 0/1 |
| `["guardrails"]` | Laporan guardrail `{passed, reason, document, pages, checks, skipped, unavailable}` |
| `["extraction"]` | Hasil OCR `{engine, model, elapsed_ms, n_pages, full_text, pages[]}` |
| `["extraction","structuring"]` | `{document_type, total_slip, slips[{fields, source, checks, missing_mandatory_fields}], llm_used}` |
| `["extraction","structuring","scoring"]` | Sama dengan pipeline penuh, tanpa guardrail |

**Beda dengan NPWP.** Ketiga guardrail slip gaji membaca **teks OCR** (AUC 0,911; model piksel hanya
0,638), jadi `guardrails` dijalankan di tahap OCR. Akibatnya `["guardrails"]` juga menjalankan OCR, bisa
dijawab 202 bila melewati 15 detik, dan mendapat callback seperti sequence lain. `extraction` mengembalikan
teks per halaman (`pages`), bukan `blocks` per baris.

**Laporan guardrail** (`data` untuk `["guardrails"]`):

```json
{"passed": true, "reason": null,
 "document": {"verdict": "accepted", "confidence": 0.9934, "n_pages": 3, "threshold": 0.47},
 "pages": [],
 "checks": {"blank": {...}, "blur": {"p_broken": 0.0153, ...}, "identity": {"proba_slip_gaji": 0.9934, ...}},
 "skipped": [], "unavailable": []}
```

`skipped`: guardrail yang dimatikan. `unavailable`: guardrail yang menyala tetapi tidak menjawab (dilewati,
dua yang lain tetap memutuskan). Urutan penolakan: kosong > buram > bukan slip gaji.

## 4. GET /v1/extract-ocr/{request_id}

Kontrak sama dengan POST, tanpa menunggu: 200 selesai, 202 berjalan, 400 ditolak, 422 tahap gagal, 404
`REQUEST_ID_NOT_FOUND`. Sequence dan `column_confidence_threshold` dibaca dari job, jadi jawabannya sama
dengan POST.

## 5. Callback (jawaban 202)

`ORCHESTRATION_CALLBACK_FORMAT=result`: satu POST per request saat selesai ke `ORCHESTRATION_URL` +
`ORCHESTRATION_CALLBACK_PATH`, header `X-Callback-Key`. Bentuknya sama dengan NPWP:

| Keadaan | Body |
|---|---|
| Selesai | `{"request_id", "status": "completed", "result": <data jawaban 200>, "guardrails": 0}` |
| Ditolak (guardrail / aturan structuring) | `{"request_id", "status": "completed", "result": null, "guardrails": 1, "message": "<alasan>", "error_code": "DOWNSTREAM_VALIDATION_ERROR"}` |
| Gagal | `{"request_id", "status": "failed", "error_code": "<TAHAP>_FAILED", "message": "<alasan>"}` |

`result` sama persis dengan `data` jawaban 200 untuk request itu: `{total_slip, slip[]}` dengan confidence
**0/1** memakai `column_confidence_threshold` request; sequence yang berhenti lebih awal: hasil tahap
terakhirnya apa adanya. Callback yang dijawab 5xx / tidak terjangkau dicoba ulang (outbox) sampai
`ORCHESTRATION_CALLBACK_MAX_AGE_SECONDS` (600); 409 `RESULT_NOT_READY` dikirim ulang 5× tiap 1,5 detik.
Mode poll: `ORCHESTRATION_CALLBACK_ENABLED=false`, tidak ada callback; baca `GET /v1/extract-ocr/{request_id}`.
Pengiriman at-least-once, bisa tidak berurutan; perlakukan idempoten.

## 6. Error Codes

| HTTP | `errors` | Penyebab |
|---|---|---|
| 400 | `EMPTY_FILE` / `UNSUPPORTED_FILE_TYPE` / `UNREADABLE_FILE` / `TOO_MANY_PAGES` / `INVALID_FILE_SOURCE` / `FILE_URL_REJECTED` | Berkas ditolak di pintu masuk |
| 400 | `UNSUPPORTED_DOCUMENT_TYPE` | `document_type` bukan `slip_gaji` |
| 400 | `DOWNSTREAM_VALIDATION_ERROR` | Ditolak guardrail (`pipeline_last_stage: guardrails`) atau aturan structuring; alasan di `message` |
| 401 | `UNAUTHORIZED` | `X-API-Key` salah / tidak ada |
| 404 | `REQUEST_ID_NOT_FOUND` | GET request_id tidak dikenal |
| 413 | `FILE_TOO_LARGE` | Lebih dari 2,5 MB |
| 422 | `VALIDATION_ERROR` / `INVALID_PIPELINE_SEQUENCE` / `INVALID_THRESHOLD` | Parameter tidak valid; tidak ada yang jalan |
| 422 | `OCR_FAILED` / `STRUCTURING_FAILED` / `SCORING_FAILED` | Tahap gagal; alasan di `message` |
| 500 | `DOWNSTREAM_SERVER_ERROR` | Service internal menjawab di luar kontrak; aman dikirim ulang |
| 500 | `INTERNAL_SERVER_ERROR` | Galat tak terduga di orchestrator; aman dikirim ulang |
| 503 | `DOWNSTREAM_UNAVAILABLE` | Service internal tidak terjangkau; aman dikirim ulang |
| 504 | `DOWNSTREAM_TIMEOUT` | Service internal tidak menjawab; aman dikirim ulang |

Setiap error membawa `pipeline_last_stage`; penolakan di pintu masuk selalu `orchestrator`.

## 7. Basis data

Cloud SQL database **`nilam`** (`edm-bribrain-dev-01:asia-southeast2:gc-bribrain-dev-sql-psql-01`, dipakai bersama
dokumen NILAM lain seperti SHM), skema **`nilam_ocr_slipgaji`**: tabel job/hasil per tahap
`nilam_{ocr,structuring,scoring}_{jobs,results}`, `nilam_pipeline_outbox`, `nilam_guardrails_results`, kembaran
`nilam_testing_*` untuk endpoint `-test`, versi migrasi `nilam_ocr_slipgaji_alembic_version`, dan
**`system_prompt`** (DDL tim: `version`, `system_prompt`, `is_active`, `change_note`, `created_at`; paling banyak
satu baris aktif) untuk prompt LLM bila `PROMPT_SOURCE=db`. Semua dibuat migrasi `db/` (0010 memindahkan tabel
lama dari `public` beserta isinya).

## 8. Endpoint internal tanpa pipeline

Untuk pengujian tim ML, tidak dipanggil Orkestrasi: `POST /v1/structuring-direct` (structuring, body
`{document_type, ocr}`) dan `POST /v1/scoring-direct` (scoring), sinkron, tanpa job dan tanpa basis data.
