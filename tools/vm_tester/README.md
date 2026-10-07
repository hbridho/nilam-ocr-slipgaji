# VM tester (lokal)

Halaman kecil di laptop untuk menguji slip gaji yang berjalan di VM `gc-bribrain-dev-gce-facematch-01`:
unggah slip → dikirim ke `POST /v1/extract-ocr` orchestrator di VM → hasilnya ditampilkan per field
(nilai + confidence), dengan cek `/health` dan polling otomatis bila jawabannya 202.

```
browser  ->  vm_tester :8099 (laptop)  ->  tunnel 127.0.0.1:18034  ->  VM orchestrator :8034  ->  pipeline
```

API key hanya ada di `.env` laptop; browser tidak pernah melihatnya.

Sumbernya `D:\OCR\slip_gaji\vm_tester` di laptop; salinannya ikut ke repo GitHub sebagai `tools/vm_tester/` setiap
kali `regular_updates\make_update.ps1` dijalankan. Dari clone repo, jalankan dari folder `tools\vm_tester`.

## 1. Buka tunnel ke VM (pilih satu)

**A. VS Code Remote-SSH (paling mudah, saat terhubung ke `facematch`):**
panel bawah → tab **PORTS** → **Forward a Port** → ketik `8034` → setelah muncul, klik kanan baris itu →
**Change Local Address Port** → `18034`. Tunnel hidup selama jendela Remote-SSH terbuka.

**B. gcloud IAP (tanpa VS Code), di terminal laptop yang dibiarkan terbuka:**
```powershell
gcloud compute start-iap-tunnel gc-bribrain-dev-gce-facematch-01 8034 --local-host-port=localhost:18034 --zone asia-southeast2-a --project edm-bribrain-dev-01
```

Cek: `curl http://127.0.0.1:18034/health` → `"status":"healthy"`.

## 2. Isi `.env` (sekali)

```powershell
cd D:\OCR\slip_gaji\vm_tester
copy .env.example .env
notepad .env        # VM_API_KEY = API_KEY dari VM:  grep ^API_KEY= /opt/nilam-ocr-slipgaji/deploy/vm/.env
```

## 3. Jalankan

```powershell
cd D:\OCR\slip_gaji\vm_tester
D:\OCR\slip_gaji\nilam-ocr-slipgaji\.venv\Scripts\python.exe app.py
```

Buka http://127.0.0.1:8099 → pilih berkas slip gaji → **Kirim ke VM**.

- Status VM hijau `healthy` = tunnel dan orchestrator OK; merah = tunnel mati atau service di VM belum jalan.
- `HTTP 200` = hasil langsung; `HTTP 202` = diproses, halaman menunggu hasilnya otomatis (sampai 3 menit);
  `HTTP 400` + guardrails ditolak (1) = bukan slip gaji / buram / kosong; `401` = API key salah.
- Kosongkan centang di *pipeline_name_sequence* untuk menjalankan sebagian (mis. hanya guardrails).
