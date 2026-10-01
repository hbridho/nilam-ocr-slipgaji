#!/usr/bin/env python3
"""Salin pipeline & model ML dari repo penelitian ke libs/slip_ml/slip_ml/vendor.

    python service/scripts/sync_ml.py            # periksa saja, laporkan yang berbeda
    python service/scripts/sync_ml.py --apply    # salin

Kenapa disalin apa adanya, bukan ditulis ulang: featuriser sisi latih dan sisi saji harus
berkas yang SAMA. Model keyakinan yang lama pernah punya dua salinan featuriser dan keduanya
diam-diam bergeser satu kolom. Skrip ini membuat pergeseran itu kelihatan: tanpa --apply ia
hanya memberi tahu berkas mana yang sudah beda dengan sumbernya.
"""

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICE = HERE.parent
REPO = SERVICE.parent
VENDOR = SERVICE / "libs" / "slip_ml" / "slip_ml" / "vendor"

# (sumber relatif ke repo, tujuan relatif ke vendor)
#
# Folder pipeline-nya `ocr_main/` di repo penelitian tetapi `slip_gaji_main/` di vendor: di dalam
# service, "ocr" tidak membedakan apa pun — semuanya tentang OCR — sementara dokumennya
# membedakan. Isi berkasnya tetap disalin apa adanya, byte demi byte.
FILES = [
    # pipeline extraction
    ("ocr_main/s1_ocr.py", "slip_gaji_main/s1_ocr.py"),
    ("ocr_main/s2_structure.py", "slip_gaji_main/s2_structure.py"),
    # s3_run.py TIDAK disalin: runner CLI penelitian (menyapu input/, menulis CSV, mencetak tabel
    # perbandingan). Tidak satu pun service mengimpornya — yang dipakai saat menyaji adalah s1_ocr
    # dan s2_structure, lewat slip_ml.ocr dan slip_ml.structure.
    ("ocr_main/config.yaml", "slip_gaji_main/config.yaml"),
    # Teks prompt, terpisah dari config.yaml supaya nanti bisa pindah ke basis data tanpa
    # membongkar berkas setelan. Satu berkas per versi; versinya disebut di config.yaml dan ikut
    # tercatat bersama hasil ekstraksi, jadi versi lama tetap disalin selama masih dirujuk.
    ("ocr_main/prompts/slip_gaji.v1.md", "slip_gaji_main/prompts/slip_gaji.v1.md"),
    ("ocr_main/core/__init__.py", "slip_gaji_main/core/__init__.py"),
    ("ocr_main/core/config.py", "slip_gaji_main/core/config.py"),
    ("ocr_main/core/fields.py", "slip_gaji_main/core/fields.py"),
    # Pemilih backend LLM. Yang membuat "Bedrock pindah host" menjadi perubahan setelan, bukan
    # perubahan kode — dan yang menyediakan backend `http` untuk server model sendiri.
    ("ocr_main/core/llm.py", "slip_gaji_main/core/llm.py"),
    ("ocr_main/core/bedrock.py", "slip_gaji_main/core/bedrock.py"),
    ("ocr_main/core/ocr_api.py", "slip_gaji_main/core/ocr_api.py"),
    # guardrail (teks + piksel) — featuriser dipakai bersama sisi latih
    ("scripts/guard_features.py", "ml/guard_features.py"),
    ("scripts/guard_struct.py", "ml/guard_struct.py"),
    ("scripts/guard_score.py", "ml/guard_score.py"),
    # gerbang mutu: blank & blur, dari ciri mutu OCR saja
    ("scripts/guard_quality_score.py", "ml/guard_quality_score.py"),
    # skor keyakinan per field
    ("scripts/conf_build.py", "ml/conf_build.py"),
    ("scripts/conf_score.py", "ml/conf_score.py"),
    # model terlatih
    ("scripts/models/guard_text.json", "ml/models/guard_text.json"),
    ("scripts/models/guard_pix.json", "ml/models/guard_pix.json"),
    ("scripts/models/guard_quality.json", "ml/models/guard_quality.json"),
    ("scoring/models/conf.json", "ml/models/conf.json"),
]

# core/bedrock.env TIDAK ikut: kredensial tidak masuk ke image. Service membacanya dari
# environment (ENABLE_LLM + kredensial AWS biasa).
EXCLUDED = ("ocr_main/core/bedrock.env",)


def digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="benar-benar menyalin")
    args = ap.parse_args()

    changed, missing = [], []
    for src_rel, dst_rel in FILES:
        src, dst = REPO / src_rel, VENDOR / dst_rel
        if not src.is_file():
            missing.append(src_rel)
            continue
        if digest(src) == digest(dst):
            continue
        changed.append((src_rel, dst_rel, "baru" if not dst.exists() else "beda"))
        if args.apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    for name in missing:
        print(f"  HILANG di repo: {name}")
    for src_rel, dst_rel, how in changed:
        print(f"  {'disalin' if args.apply else how:>8}  {src_rel} -> vendor/{dst_rel}")
    if not changed and not missing:
        print(f"vendor cocok dengan repo ({len(FILES)} berkas)")
    elif not args.apply and changed:
        print(f"\n{len(changed)} berkas berbeda — jalankan dengan --apply untuk menyalin")
    print(f"tidak pernah disalin (kredensial): {', '.join(EXCLUDED)}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
