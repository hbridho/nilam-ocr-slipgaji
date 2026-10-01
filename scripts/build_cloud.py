#!/usr/bin/env python3
"""Build dan push image lewat Google Cloud Build — tanpa docker di mesin ini.

    python scripts/build_cloud.py --dry-run              # cetak perintahnya, jangan jalankan
    python scripts/build_cloud.py                        # ketujuh service
    python scripts/build_cloud.py structuring scoring     # sebagian saja
    python scripts/build_cloud.py --tag v1.2.3

KENAPA ADA JALUR INI. `deploy/helm/deploy.sh` membangun image di mesin Anda: ia butuh `docker` dan
`bash`. Cloud Build memindahkan build-nya ke Google, jadi yang perlu ada di mesin Anda hanya
`gcloud`. Bonusnya bukan kenyamanan saja: Cloud Build membangun di Linux amd64, persis target GKE,
sehingga wheel di requirements.lock (dikompilasi untuk x86_64-unknown-linux-gnu, Python 3.11)
diselesaikan di tempat yang sama dengan yang akan menjalankannya. Build di laptop Windows tidak
pernah menguji itu.

Ditulis dengan Python, bukan .ps1: ExecutionPolicy `Restricted` menolak skrip PowerShell, dan
`bash` tidak ada di Windows. Pola yang sama dengan local/run_local.py.

YANG TIDAK DIKERJAKAN SKRIP INI: `helm upgrade`. Build dan deploy sengaja dipisah — perintah helm
yang sesuai dicetak di akhir, dengan tag yang baru saja dibangun.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "deploy" / "gke" / "cloudbuild.yaml"


def find_gcloud() -> str | None:
    """Peluncur gcloud yang benar-benar bisa dieksekusi, atau None.

    Di Windows, Cloud SDK memasang TIGA berkas bernama gcloud di bin/: `gcloud` (shell script Unix),
    `gcloud.cmd`, dan `gcloud.ps1`. `shutil.which("gcloud")` mengembalikan yang pertama — nama
    tanpa ekstensi itu ada, jadi ia berhenti di situ — dan CreateProcess tidak bisa menjalankannya:
    FileNotFoundError, padahal gcloud jelas terpasang. Jadi `.cmd` dicari lebih dulu.
    """
    names = ("gcloud.cmd", "gcloud.exe", "gcloud") if os.name == "nt" else ("gcloud",)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def gcloud_out(gcloud: str, *args: str) -> str:
    try:
        done = subprocess.run([gcloud, *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


def preflight(gcloud: str, project: str | None) -> list[str]:
    """Apa yang kurang sebelum submit, dengan cara memperbaikinya.

    Diperiksa di sini, bukan dibiarkan gcloud yang mengeluh: tanpa ini satu-satunya petunjuk datang
    setelah context diunggah, dan mudah tertukar dengan masalah izin Artifact Registry — dua hal
    yang penyelesaiannya sangat berbeda.
    """
    missing = []
    if not gcloud_out(gcloud, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"):
        missing.append("belum ada akun aktif        ->  gcloud auth login")
    if not project:
        current = gcloud_out(gcloud, "config", "get-value", "project")
        if not current or current == "(unset)":
            missing.append("project belum di-set        ->  gcloud config set project <PROJECT_ID>")
    return missing


def services_in(config: dict) -> list[str]:
    return [step["id"] for step in config.get("steps", [])]


def default_tag() -> str:
    """SHA commit pendek kalau git ada dan working tree bersih; kalau tidak, cap waktu UTC.

    Cap waktu diberi akhiran `-dirty` supaya tidak pernah tertukar dengan tag yang bisa dilacak ke
    satu commit — sebuah image yang tidak bisa dilacak ke kode yang membuatnya adalah image yang
    tidak bisa di-rollback dengan yakin.
    """
    if shutil.which("git"):
        try:
            sha = subprocess.run(
                ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            dirty = subprocess.run(
                ["git", "-C", str(REPO), "status", "--porcelain"], capture_output=True, text=True, check=True
            ).stdout.strip()
            return f"{sha}-dirty" if dirty else sha
        except subprocess.CalledProcessError:
            pass
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-dirty"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("services", nargs="*", help="kosong = semuanya")
    ap.add_argument("--tag", help="bawaan: SHA commit pendek, atau cap waktu + '-dirty'")
    ap.add_argument("--project", help="--project untuk gcloud; bawaan: project aktif gcloud")
    ap.add_argument(
        "--no-push",
        action="store_true",
        help="bangun saja, JANGAN push ke Artifact Registry (buat membuktikan image-nya bisa dibangun)",
    )
    ap.add_argument("--dry-run", action="store_true", help="cetak perintahnya, jangan jalankan")
    args = ap.parse_args()

    if not CONFIG.is_file():
        sys.exit(f"tidak ada: {CONFIG}")
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    known = services_in(config)

    wanted = args.services or known
    unknown = [s for s in wanted if s not in known]
    if unknown:
        sys.exit(f"service tidak dikenal: {', '.join(unknown)}\nyang ada: {', '.join(known)}")

    if not (REPO / ".gcloudignore").is_file():
        print("PERINGATAN: .gcloudignore tidak ada — gcloud akan mengunggah .venv juga (~463 MB).")

    gcloud = find_gcloud()
    if gcloud is None and not args.dry_run:
        sys.exit("gcloud tidak ada di PATH. Pasang Google Cloud SDK, atau pakai --dry-run.")

    tag = args.tag or default_tag()
    config_path = CONFIG

    # Config sementara, supaya cloudbuild.yaml tetap satu-satunya sumber dan tidak perlu
    # diduplikasi per kombinasi. Dibuat kalau hanya sebagian service yang diminta, atau kalau
    # push dimatikan.
    #
    # `--no-push` bekerja dengan MEMBUANG blok `images:`. Di Cloud Build, blok itulah yang
    # memerintahkan push; tanpa itu image dibangun lalu dibuang bersama worker-nya. Jadi ini
    # benar-benar "build saja" — bukan push yang diam-diam tetap terjadi lalu disembunyikan.
    tmp = None
    if set(wanted) != set(known) or args.no_push:
        trimmed = dict(config)
        trimmed["steps"] = [s for s in config["steps"] if s["id"] in wanted]
        if args.no_push:
            trimmed.pop("images", None)
        else:
            trimmed["images"] = [i for i in config["images"] if any(f"-{s}:" in i for s in wanted)]
        tmp = Path(tempfile.mkdtemp()) / "cloudbuild.yaml"
        tmp.write_text(yaml.safe_dump(trimmed, sort_keys=False), encoding="utf-8")
        config_path = tmp

    cmd = [
        gcloud or "gcloud",
        "builds",
        "submit",
        "--config",
        str(config_path),
        f"--substitutions=_TAG={tag}",
        str(REPO),
    ]
    if args.project:
        cmd.insert(3, f"--project={args.project}")

    print(f"service : {', '.join(wanted)}")
    print(f"tag     : {tag}\n")
    print("  " + " ".join(cmd) + "\n")

    if args.dry_run:
        print("(uji coba — tidak dijalankan)")
        if tmp:
            print(f"config sementara: {tmp}")
        return 0

    kurang = preflight(gcloud, args.project)
    if kurang:
        print("BELUM BISA DIJALANKAN:")
        for item in kurang:
            print(f"  - {item}")
        print("\nTidak ada yang diunggah. Jalankan perintah di atas, lalu ulangi.")
        return 1

    result = subprocess.run(cmd, cwd=REPO)
    if result.returncode != 0:
        print("\nBuild GAGAL. Kalau pesannya soal izin atau repository, dua sebab tersering:")
        print("  - repository Artifact Registry belum ada, atau")
        print("  - service account Cloud Build belum punya peran artifactregistry.writer")
        return result.returncode

    if args.no_push:
        print("\nSelesai. Ketujuh image TERBUKTI bisa dibangun, dan tidak ada yang di-push.")
        print("Tidak ada yang bisa di-deploy dari sini: ulangi tanpa --no-push, atau serahkan")
        print("push-nya ke pemegang kredensial Artifact Registry.")
        return 0

    print("\nSelesai. Deploy tag ini tanpa membangun ulang:\n")
    sets = " ".join(f"--set services.{s}.image.tag={tag}" for s in wanted)
    print("  helm -n nilam-ocr-slipgaji upgrade nilam-ocr-slipgaji deploy/helm/nilam-ocr-slipgaji \\")
    print(f"    -f deploy/helm/nilam-ocr-slipgaji/values-<lingkungan>.yaml {sets}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
