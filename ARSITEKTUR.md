# Arsitektur layanan OCR slip gaji

Tujuh service HTTP, satu repo Bitbucket per service (`ms-bribrain-nilam-ocr-slipgaji-<service>`). Dokumen masuk sebagai berkas, keluar sebagai slip gaji terstruktur
beserta skor keyakinan per nilai. Dokumen ini menjelaskan **alurnya**, **isi tiap service**, dan
**apa yang bisa diganti tanpa menyentuh kode**.

> Disimpan di akar, bukan di `docs/`, karena `docs/` ada di `.gitignore` — berkas di sana tidak
> akan pernah ikut ke GitHub.

---

## 1 · Alur

Ini **rantai serah-terima**, bukan hub. Tiap tahap menyerahkan hasilnya ke tahap berikutnya;
orchestrator hanya memulai dan ditanya hasilnya.

```text
         POST /v1/extract-ocr
                 │
          orchestrator :8034 ──────────────── GET /v1/extract-ocr/{request_id}
                 │                                      (status / hasil akhir)
                 ▼
          extraction :8030          berkas → teks OCR per halaman + ringkasan skor
                 │
                 ├──────────► guardrail-blank    :8035   kosong?     aturan panjang teks
                 ├──────────► guardrail-blur     :8036   terbaca?    6 ciri mutu OCR
                 └──────────► guardrail-identity :8037   slip gaji?  TF-IDF + tata letak
                 │            dipanggil BERSAMAAN, digabung: kosong > buram > identitas
                 │            satu mati / dimatikan → dua lainnya tetap memutuskan
                 ▼
          structuring :8032         teks → 20 field per slip (aturan, + arbitrase LLM opsional)
                 │
                 ▼
          scoring :8033             tiap nilai → P(nilai ini benar), 0-1 → confidence 0/1
                 │
                 ▼
            hasil akhir
```

**Guardrail berjalan SETELAH OCR**, bukan sebelumnya. Alasannya terukur: menilai dari teks jauh
lebih baik daripada dari piksel (AUC 0,911 lawan 0,638). Konsekuensinya setiap dokumen dibayar
OCR-nya dulu, termasuk yang nanti ditolak — itu harga yang disengaja.

**Ketiga guardrail adalah tiga service terpisah dan tetap terpisah.** Tidak ada satu service
pembungkus di depan ketiganya. Yang memanggil dan menggabungkan ketiga jawaban adalah extraction
(`ocr_common.clients.guardrails.GuardrailsFanout`), tepat setelah OCR.

Di kontrak API ([07]) namanya tetap `guardrails`: `pipeline_name_sequence` memakai
`guardrails → extraction → structuring → scoring`, dan penolakannya dilaporkan `pipeline_last_stage:
guardrails`. Hanya urutan eksekusinya yang berbeda dari NPWP.

### Kenapa dipanggil bersamaan, dan apa yang hilang karenanya

Satu proses dulu memeriksanya berurutan dengan hubungan pendek: halaman kosong langsung dijawab
`blank`, dan pemeriksaan mutu serta identitas tidak pernah dijalankan. Tiga service yang berdiri
sendiri kehilangan hubungan pendek itu — mutu dan identitas tetap menjawab meski halamannya
kosong, dan jawaban mereka di situ tidak berarti apa-apa.

Yang mendahulukan `blank` sekarang **penggabungnya**, bukan urutan pemanggilan:

| urutan | putusan | arti bagi pengguna |
|---|---|---|
| 1 | `blank` | halaman tidak ada teksnya — unggah halaman yang benar |
| 2 | `blur` | terlalu rusak untuk dibaca — foto ulang lebih jelas |
| 2 | `mutu_tak_terukur` | **ringkasan skor OCR tidak dikirim** — yang perlu diperbaiki permintaannya, bukan fotonya |
| 3 | `bukan_slip_gaji` | unggah dokumen yang benar |
| — | `slip_gaji` | lanjut |

**Guardrail yang mati atau dimatikan.** Yang dimatikan (`GUARDRAIL_<NAMA>_ENABLED=false`, atau Helm
`services.<nama>.enabled=false`) tidak ditanya dan tercatat di `skipped`. Yang menyala tetapi tidak
menjawab tercatat di `unavailable` dan dilewati selama `GUARDRAILS_FAIL_OPEN=true` (bawaan); dua yang lain
tetap memutuskan. `GUARDRAILS_FAIL_OPEN=false` membalik pilihan itu: job OCR gagal 503/504. Terbukti di
lokal: ketiganya dimatikan satu per satu, pipeline tetap 200.

Terukur di lokal: paralel ±7 ms jam dinding, berurutan ±24 ms.

---

## 2 · Ringkasan: port, isi, yang bisa diganti

| Service | Port | Isi | Diganti lewat |
|---|---|---|---|
| orchestrator | 8034 | pintu masuk, cek berkas/urutan/threshold, penunggu hasil | `*_SERVICE_URL`, `PIPELINE_WAIT_SECONDS` (15), `MAX_DOCUMENT_PAGES` (12), `FIELD_CONFIDENCE_THRESHOLD` (0,5) |
| extraction | 8030 | render PDF → gambar → teks OCR → ketiga guardrail | `EXTRACTION_BACKEND` = `api` \| `rapidocr` \| `mock`, `GUARDRAIL_<NAMA>_ENABLED/_URL`, `GUARDRAILS_FAIL_OPEN` |
| guardrail-blank | 8035 | aturan panjang teks | `BLANK_BACKEND`, `BLANK_MAX_CHARS` |
| guardrail-blur | 8036 | regresi logistik 6 ciri mutu OCR | `BLUR_BACKEND`, `BLUR_THRESHOLD` |
| guardrail-identity | 8037 | TF-IDF 1–2 gram + 8 ciri tata letak | `IDENTITY_BACKEND`, `IDENTITY_REJECT_THRESHOLD`, `IDENTITY_THRESHOLD_URL` |
| structuring | 8032 | aturan 20 field + arbitrase LLM | `STRUCTURING_BACKEND`, `ENABLE_LLM`, `LLM_BACKEND`, `PROMPT_SOURCE` (`file` \| `db`) |
| scoring | 8033 | regresi logistik keyakinan per nilai, 0-1 | `SCORING_BACKEND`, `FIELD_CONFIDENCE_THRESHOLD` |

Pola yang sama di ketujuhnya: satu dict `BACKENDS` di `app/dependencies.py`, dipilih dengan
`build_backend()` dari satu variabel lingkungan, dibangun malas (`lru_cache`). Menambah
implementasi berarti menambah satu baris di dict itu — tidak ada tempat lain yang membangun
objek-objek ini. Setiap `get_*` di `dependencies.py` juga titik yang ditimpa tes lewat
`app.dependency_overrides[...]`.

**`mock` ditolak di luar `ENVIRONMENT=local`.** Guardrail tiruan berarti tidak ada penjaga sama
sekali, dan itu tidak boleh lolos ke lingkungan nyata karena salah ketik.

---

## 3 · Service satu per satu

### orchestrator :8034

**Isi.** Dua route: `POST /v1/extract-ocr` (unggah; menjawab hasil, atau 202 kalau belum siap)
dan `GET /v1/extract-ocr/{request_id}` (di mana permintaan itu sekarang, tanpa menunggu). Ia
memegang job dan catatan putusan guardrail.

**Flow.** Terima berkas → periksa jenis, ukuran, jumlah halaman, `pipeline_name_sequence` dan threshold
**sebelum** apa pun dijalankan → serahkan ke extraction → tunggu sampai service terakhir urutannya selesai,
paling lama `PIPELINE_WAIT_SECONDS` (15 s) → jawab hasil atau 202.

**Yang bisa diganti.** Alamat tiap tahap (`EXTRACTION_SERVICE_URL`, …), batas halaman
(`MAX_DOCUMENT_PAGES`, 12), lama tunggu, dan jumlah percobaan ulang.

### extraction :8030

**Isi.** Pemecah PDF jadi gambar per halaman, lalu satu dari tiga sumber teks.

| backend | dari mana teksnya | catatan |
|---|---|---|
| `api` (bawaan) | layanan OCR paddle6 (PP-OCRv6) lewat HTTP | **tidak ada model di dalam image**; `EXTRACTION_OCR_URL` wajib |
| `rapidocr` | model ONNX lokal | image jauh lebih besar |
| `mock` | teks tetap | hanya `ENVIRONMENT=local` |

**Flow.** Berkas → gambar (`EXTRACTION_RENDER_DPI`) → satu panggilan OCR per halaman → teks +
ringkasan skor (`mean`, `min`, `n_boxes`, `n_low`) → ketiga guardrail bersamaan (bila `guardrails` ada di
urutan) → serahkan ke structuring, atau akhiri permintaan bila urutannya berhenti di sini.

**Yang bisa diganti.** Sumber OCR seluruhnya, alamat dan batas waktunya, DPI, batas halaman (20).
`EXTRACTION_OCR_URL` sengaja **tidak** diperiksa localhost-nya: layanan OCR boleh jadi sidecar di
pod yang sama.

**Kebijakan yang perlu disadari.** `GUARDRAILS_FAIL_OPEN=true` (bawaan): guardrail tumbang →
dokumen dinilai dua guardrail lainnya, laporan mencatat `unavailable`. Menahan seluruh lalu lintas
OCR karena satu penjaga tumbang dinilai lebih merugikan. `false` membalik pilihan itu.

### guardrail-blank :8035

**Isi.** Satu aturan: `chars <= BLANK_MAX_CHARS` (20). **Tidak ada model, tidak ada bobot.**

**Yang bisa diganti.** Hanya ambangnya. Pemeriksaan ini sengaja tidak mendahulukan dirinya —
jawabannya tetap diberikan, dan yang memutuskan urutan adalah penggabung.

### guardrail-blur :8036

**Isi.** Regresi logistik atas **6 ciri mutu OCR** (rata-rata, minimum, porsi kotak berskor
rendah, `n_boxes`, `chars`, `chars/box`). AUC 0,985, ambang 0,9584, model `guard_quality.json`.

**Yang penting.** Ia **tidak membaca satu kata pun** — hanya skor. Itu disengaja: dokumen buram
sering menghasilkan banyak karakter yang semuanya salah.

**Tanpa `confidence`,** empat dari enam ciri menjadi nol — nilai yang sama yang diberikan halaman
hancur. Teks slip yang bersih memberi `p_broken` 0,9999. Karena itu jawabannya `mutu_tak_terukur`,
bukan `blur`: yang kurang permintaannya, bukan fotonya.

### guardrail-identity :8037

**Isi.** TF-IDF unigram+bigram + 8 ciri tata letak baris, kalibrasi Platt. AUC 0,911, ambang 0,47,
model `guard_text.json`.

**Yang bisa diganti.** Ambangnya, dan ini satu-satunya guardrail yang menerima ambang **milik
Orkestrasi pusat** (`IDENTITY_THRESHOLD_URL` menjawab `{"threshold": x}` seperti NPWP, di-cache 60 s; per
permintaan `guardrails_confidence_threshold={"acc_rej": x}`) — karena hanya skor inilah yang
berarti P(slip gaji). Dua yang lain tidak, dan tidak seharusnya.

### structuring :8032

**Isi.** Dua lapis:

| lapis | apa | akurasi field |
|---|---|---|
| aturan | sinonim label, pembacaan kolom, turunan aritmetika (gaji bersih = total pendapatan − total potongan) | 81,5% |
| LLM | arbitrase nilai uang | 89,9% |

Satu halaman = satu slip; berkas yang memuat tiga bulan menghasilkan tiga slip. Menggabungkan
teksnya akan mencampur komponen satu bulan ke total bulan lain.

**LLM mati secara default** (`ENABLE_LLM=false`): service harus bisa berjalan tanpa kredensial
AWS, dan hasil yang lebih rendah tapi jujur lebih baik daripada service yang menolak start.

**Yang bisa diganti — lihat §4.** Penyedia LLM, alamatnya, modelnya, dan promptnya, semuanya dari
environment.

### scoring :8033

**Isi.** Regresi logistik, 35 kolom inti + 20 one-hot per field. Gini 0,6908. Populasinya **setiap
nilai keluaran**, bukan populasi tabel evaluasi.

**Keluaran.** Untuk tiap nilai: P(nilai ini benar), **skala 0-1** (API spec [07]; berkas penelitian
yang di-vendor menghitung 0-100 dan dibagi 100 di `slip_ml.confidence`). Kontrak memetakan ke
`confidence` 0/1 dengan `column_confidence_threshold` permintaan, lalu `FIELD_CONFIDENCE_THRESHOLD`
(0,5). Callback membawa probabilitas mentahnya.

---

## 4 · Mengganti Bedrock, dan memindahkan prompt ke basis data

Semuanya setelan. Tidak ada kode yang perlu disunting, dan **image tidak perlu dibangun ulang.**

### Penyedia LLM

```bash
# Bedrock (bawaan) — kredensial dari environment, tidak ada kunci di dalam image
LLM_BACKEND=bedrock
LLM_REGION=ap-southeast-3            # opsional: gateway internal / cloud berdaulat
LLM_ENTRA_TOKEN_URL=...              # opsional
LLM_MANTLE_URL=...                   # opsional

# Pindah ke server model sendiri — vLLM, TGI, LiteLLM, gateway internal
LLM_BACKEND=http
LLM_ENDPOINT=http://vllm.internal/v1/chat/completions
LLM_MODEL=qwen3-235b
LLM_API_KEY_ENV=NAMA_VAR_YANG_MEMUAT_KUNCI   # NAMA variabelnya, bukan kuncinya

# Tidak boleh menembak ke luar sama sekali
LLM_BACKEND=off
```

Bentuk permintaan backend `http` adalah chat-completions ala OpenAI — yang dipakai vLLM, TGI,
Ollama (mode kompatibel) dan sebagian besar gateway internal. Jadi "pindah ke server sendiri"
biasanya cukup mengisi `LLM_ENDPOINT`.

**Penyedia yang bentuknya lain** didaftarkan tanpa menyunting kode inti:

```python
from core import llm
llm.register_chat_backend("punyaku", lambda cfg: KlienSaya(cfg["endpoint"]))
```

Backend yang tidak terdaftar, atau `http` tanpa `LLM_ENDPOINT`, membuat service **menolak
start** — bukan gagal di permintaan pertama. Setelan yang salah harus terlihat saat deploy.

### Prompt

Prompt **tidak pernah ada di dalam setelan**. Yang ada penunjuknya:

```bash
PROMPT_SOURCE=file
PROMPT_VERSION=1
PROMPT_PATH=/etc/prompts/slip_gaji.v2.md    # boleh absolut -> ConfigMap / volume
```

Bawaannya `services/structuring/prompts/slip_gaji.v1.md`, di luar kode, disalin ke `/app/prompts` di image. Karena `PROMPT_PATH` boleh absolut,
mengganti prompt bisa dilakukan dengan memasang volume — tanpa rebuild.

**Basis data sudah tersedia**: structuring mendaftarkan resolver `db` sendiri (`app/prompts.py`).
`PROMPT_SOURCE=db` membaca tabel `nilam_ocr_slipgaji.system_prompt` (DDL yang disepakati tim untuk semua
dokumen NILAM, migrasi 0010): `PROMPT_VERSION` kosong berarti baris `is_active = TRUE` (indeks unik menjamin
paling banyak satu), angka berarti kolom `version` — sekali saat start. Basis data tidak terbaca → kembali ke `PROMPT_PATH` dengan
peringatan (`PROMPT_DB_FALLBACK_TO_FILE=true`), atau menolak start bila `false`. Teks prompt sengaja tidak ditaruh di `config.yaml` justru supaya
perpindahan ini bukan penulisan ulang berkas setelan — dan supaya setiap suntingan prompt tidak
masuk ke riwayat yang sama dengan DPI dan pilihan model. Dua hal yang berubah karena alasan
berbeda dan dengan laju berbeda.

**Versinya ikut tercatat.** `PROMPT_META` (`source`, `name`, `version`) dilaporkan saat start dan
dicatat di log, sehingga sebuah hasil selalu bisa dilacak ke prompt yang menghasilkannya. Ini
bukan kemewahan: prompt yang salah versi tetap menghasilkan jawaban yang kelihatan masuk akal,
jadi log adalah satu-satunya cara mengetahuinya.

Prompt kosong atau berkas yang tidak ada **ditolak**, dengan alasan yang sama.

### Model

Semua model di jalur sajian adalah **JSON di dalam `libs/slip_ml`** — `guard_quality.json`,
`guard_text.json`, `conf.json`. Tidak ada torch, sklearn, pickle, atau OpenCV di image mana pun;
penyajiannya numpy saja. Memperbarui model berarti mengganti satu berkas JSON.

Model yang sama bisa dibaca dari **GCS** saat start, seperti NPWP: `<SERVICE>_MODEL_GCS_URI` menunjuk
`gs://gc-bribrain-dev-gcs-ocr-nilam-01/nilam-ocr-slipgaji/<guardrails|scoring>/<model>/v<N>/<model>_v<N>.json`
(tata letak folder SHM: satu folder per model dan versi, `manifest.json` di sebelahnya). Berkas diunduh ke
`/tmp/models`, dicek MD5 / SHA-256, lalu loader `slip_ml` diarahkan ke sana (`slip_ml.models.use`) tanpa
menyunting kode vendor. Unduhan gagal = service menolak start. Folder unggahnya dibuat
`scripts/export_gcs_models.py`.

---

## 5 · Yang masih terbuka

| Hal | Keadaan |
|---|---|
| **Target deploy** | Dev: VM `gc-bribrain-dev-gce-facematch-01` dengan Docker (`deploy/vm/`); Helm untuk GKE tetap setara NPWP. |
| **Layanan OCR** | `EXTRACTION_OCR_URL` harus terjangkau dari VM / namespace; uji end-to-end lokal memakai OCR tiruan karena alamat itu tidak terjangkau dari laptop. |
| **Batas halaman** | NPWP 2; slip gaji 12 (berkas tiga bulan lazim). Konfirmasi dengan Orkestrasi pusat. |
| **`["guardrails"]` saja** | Menjalankan OCR dulu (guardrail membaca teks), jadi bisa 202 + callback, beda dengan NPWP yang selalu langsung. |
| **Callback Orkestrasi** | URL, path, dan `X-Callback-Key` dari tim Orkestrasi; belum diuji ke service mereka. |

---

## 6 · Menjalankan di lokal

```powershell
.venv\Scripts\python local\run_local.py          # nyalakan ketujuhnya, tunggu sehat
.venv\Scripts\python local\coba_guardrail_3.py   # ketiga guardrail paralel + gabungan
.venv\Scripts\python local\run_local.py --stop
```

Swagger tiap service di `http://127.0.0.1:<port>/docs`. Header `X-API-Key` di semuanya.

> Salin `.env.example` ke `.env` di tiap service dan pakai `API_KEY` yang sama di ketujuhnya.
> `--port-offset 100` menjalankan stack kedua di 8130-8137 di samping yang sudah ada.
