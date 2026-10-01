"""Model dan pipeline ML slip gaji, dipakai bersama oleh kelima service.

    from slip_ml import confidence, fields, guard, ocr, structure

Isinya dua lapis. `vendor/` adalah salinan apa adanya dari repo penelitian
(service/scripts/sync_ml.py yang menyalinnya), dan modul di sini adalah pembungkus tipis yang
memberi service bentuk keluaran yang stabil. Aturannya: tidak ada logika ML yang ditulis ulang
di lapisan pembungkus — sisi latih dan sisi saji harus berkas yang sama.
"""

__all__ = ["confidence", "fields", "guard", "ocr", "structure"]
__version__ = "1.0.0"
