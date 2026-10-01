"""Berkas yang disalin apa adanya dari repo penelitian (service/scripts/sync_ml.py).

Jangan sunting isinya di sini: suntingan akan hilang pada sinkronisasi berikutnya, dan yang
lebih penting, featuriser sisi saji harus berkas yang sama persis dengan sisi latih.

Modul-modul di dalamnya saling mengimpor dengan nama datar (`import s1_ocr`, `from core import
config`, `import conf_build as CB`) karena memang dijalankan begitu di repo penelitian.
`ensure_path()` menaruh kedua foldernya di sys.path sekali, dan pemanggilnya cukup
`from slip_ml.vendor import ensure_path`.

Folder pipeline-nya bernama `slip_gaji_main` di sini, bukan `ocr_main` seperti di repo penelitian:
di dalam service, "ocr" tidak membedakan apa pun — semuanya tentang OCR — sementara dokumennya
membedakan. Beberapa berkas di `ml/` masih memuat `sys.path.insert(..., "ocr_main")`; itu tidak
diperbaiki di sini karena berkas-berkas itu harus tetap sama persis dengan sisi latih, dan
barisnya memang tidak berakibat: `ensure_path()` sudah lebih dulu memasang folder yang benar, dan
menaruh folder yang tidak ada di sys.path tidak mengubah hasil pencarian modul.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLIP_GAJI_MAIN = HERE / "slip_gaji_main"
ML = HERE / "ml"

_done = False


def ensure_path() -> None:
    """Taruh vendor/slip_gaji_main dan vendor/ml di sys.path (sekali saja, di depan)."""
    global _done
    if _done:
        return
    for folder in (ML, SLIP_GAJI_MAIN):
        path = str(folder)
        if path not in sys.path:
            sys.path.insert(0, path)
    _done = True
