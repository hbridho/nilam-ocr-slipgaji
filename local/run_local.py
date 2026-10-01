#!/usr/bin/env python3
"""Nyalakan semua service di laptop — tanpa make, tanpa Docker, tanpa urusan ExecutionPolicy.

    python local/run_local.py            # nyalakan, tunggu sampai semuanya sehat
    python local/run_local.py --status   # periksa yang sedang berjalan
    python local/run_local.py --stop     # matikan

KENAPA PYTHON, BUKAN .ps1 ATAU Makefile. Makefile repo ini ditulis untuk Linux/macOS; `make` tidak
ada di PowerShell. Dan skrip .ps1 ditolak PowerShell pada mesin yang ExecutionPolicy-nya masih
bawaan (`Restricted`) — yang berarti perintah di README gagal pada mesin yang paling umum.
Python sudah menjadi syarat repo ini, jadi penyala berbasis Python adalah satu-satunya bentuk yang
bekerja sama persis di semua tempat.

Urutannya dari yang paling dalam ke pintu masuk. Bukan keharusan teknis — tiap service start
sendiri-sendiri — tetapi kalau orchestrator dinyalakan lebih dulu, permintaan pertama gagal dengan
galat koneksi yang menyesatkan alih-alih jawaban.

Proses dilepas dari terminal ini (grup proses sendiri, keluaran ke local/logs/). Tanpa itu prompt
Anda tidak kembali sampai semua service mati, karena anak yang mewarisi pipa terminal menahannya
tetap terbuka.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOGS = REPO / "local" / "logs"
PIDFILE = LOGS / "run_local.json"

# Flag pembuatan proses di Windows, dan kenapa ketiganya perlu:
#
#   DETACHED_PROCESS          tanpa konsol sendiri, jadi tidak ada jendela yang berkedip
#   CREATE_NEW_PROCESS_GROUP  Ctrl-C di terminal ini tidak ikut menghentikan service
#   CREATE_BREAKAWAY_FROM_JOB keluar dari Job Object milik terminal
#
# Yang terakhir itu yang menentukan apakah service selamat. Windows Terminal menaruh proses
# turunannya dalam satu Job Object, dan job itu dibunuh saat pohon prosesnya dibereskan — dua flag
# pertama tidak melepaskannya dari sana, jadi tanpa yang ketiga service ikut mati begitu perintah
# ini selesai atau terminalnya ditutup. Sebagian job tidak mengizinkan breakaway; kalau begitu
# pembuatan prosesnya gagal dan kita ulangi tanpa flag itu (lihat cmd_start).
_DETACHED_PROCESS = 0x00000008
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_WINDOWS_FLAGS_NO_BREAKAWAY = subprocess.CREATE_NEW_PROCESS_GROUP | _DETACHED_PROCESS
_WINDOWS_FLAGS = _WINDOWS_FLAGS_NO_BREAKAWAY | _CREATE_BREAKAWAY_FROM_JOB

# Urut dari yang paling dalam ke pintu masuk.
#
# Ketiga guardrail berdiri sendiri-sendiri (:8035 kosong, :8036 mutu, :8037 identitas) dan dipanggil
# extraction PARALEL setelah OCR. Mematikan satu (hentikan prosesnya, atau GUARDRAIL_<NAMA>_ENABLED=false
# di services/extraction/.env) tidak memutus pipeline: dua yang lain tetap memutuskan.
SERVICES: dict[str, int] = {
    "guardrail-blank": 8035,
    "guardrail-blur": 8036,
    "guardrail-identity": 8037,
    "structuring": 8032,
    "scoring": 8033,
    "extraction": 8030,
    "orchestrator": 8034,
}


def venv_python() -> Path:
    """Python di .venv repo ini — bukan python yang kebetulan ada di PATH. `OCR_PYTHON` menimpanya."""
    if os.environ.get("OCR_PYTHON"):
        return Path(os.environ["OCR_PYTHON"])
    candidates = [REPO / ".venv" / "Scripts" / "python.exe", REPO / ".venv" / "bin" / "python"]
    for path in candidates:
        if path.is_file():
            return path
    sys.exit(
        f"Belum ada .venv di {REPO}. Jalankan dulu:\n"
        f"  python -m venv .venv\n"
        f"  .venv{os.sep}{'Scripts' if os.name == 'nt' else 'bin'}{os.sep}pip install -r requirements-dev.txt"
    )


def health(port: int, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def describe(body: dict) -> str:
    backends = body.get("backends") or {}
    return " ".join(f"{k}={v}" for k, v in backends.items())


def ports(offset: int) -> dict[str, int]:
    """The ports of this run: `--port-offset 100` runs a second stack next to one already on 8030-8037."""
    return {name: port + offset for name, port in SERVICES.items()}


def stage_urls(offset: int) -> dict[str, str]:
    """The addresses each service is given, so a shifted stack calls itself and not the other one."""
    p = ports(offset)
    base = "http://127.0.0.1"
    return {
        "EXTRACTION_SERVICE_URL": f"{base}:{p['extraction']}",
        "STRUCTURING_SERVICE_URL": f"{base}:{p['structuring']}",
        "SCORING_SERVICE_URL": f"{base}:{p['scoring']}",
        "GUARDRAIL_BLANK_URL": f"{base}:{p['guardrail-blank']}",
        "GUARDRAIL_BLUR_URL": f"{base}:{p['guardrail-blur']}",
        "GUARDRAIL_IDENTITY_URL": f"{base}:{p['guardrail-identity']}",
    }


def cmd_status(offset: int = 0) -> int:
    alive = 0
    for name, port in ports(offset).items():
        body = health(port)
        if body:
            alive += 1
            print(f"  HIDUP  {name:<14} :{port}  {describe(body)}")
        else:
            print(f"  MATI   {name:<14} :{port}")
    print(f"\n{alive}/{len(SERVICES)} service hidup")
    return 0 if alive == len(SERVICES) else 1


def cmd_stop() -> int:
    if not PIDFILE.is_file():
        print(
            "Tidak ada catatan proses (local/logs/run_local.json). "
            "Kalau ada service yang tertinggal, tutup jendelanya atau hentikan prosesnya sendiri."
        )
        return 0
    recorded = json.loads(PIDFILE.read_text(encoding="utf-8"))
    stopped = 0
    for name, pid in recorded.items():
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
            else:
                os.kill(pid, signal.SIGTERM)
            print(f"  dihentikan {name:<14} PID {pid}")
            stopped += 1
        except (ProcessLookupError, PermissionError, OSError) as exc:
            print(f"  {name:<14} PID {pid} tidak bisa dihentikan: {exc}")
    PIDFILE.unlink(missing_ok=True)
    print(f"\n{stopped} proses dihentikan.")
    return 0


def cmd_start(wait: float, offset: int = 0) -> int:
    python = venv_python()

    missing = [n for n in SERVICES if not (REPO / "services" / n / ".env").is_file()]
    if missing:
        sys.exit(
            f"Belum ada .env untuk: {', '.join(missing)}\n" f"Salin dulu dari contohnya (lihat README, langkah 2)."
        )

    already = [n for n, p in ports(offset).items() if health(p, 1.0)]
    if already:
        print(f"Sudah berjalan: {', '.join(already)}. Matikan dulu dengan --stop.")
        return 1

    LOGS.mkdir(parents=True, exist_ok=True)
    pids: dict[str, int] = {}
    env = {**os.environ, **stage_urls(offset)}
    for name, port in ports(offset).items():
        cwd = REPO / "services" / name
        args = [str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)]
        out = (LOGS / f"{name}.log").open("w", encoding="utf-8")
        # Dilepas dari terminal ini: grup proses sendiri, keluaran ke berkas. Tanpa ini prompt
        # pemanggil ditahan sampai semua service mati.
        kwargs: dict = {"cwd": cwd, "stdout": out, "stderr": subprocess.STDOUT, "env": env}
        if os.name == "nt":
            kwargs["creationflags"] = _WINDOWS_FLAGS
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(args, **kwargs)
        except OSError:
            # Job object ini tidak mengizinkan breakaway. Jalankan tanpa flag itu: service tetap
            # menyala, tetapi ikut mati ketika terminal ditutup.
            kwargs["creationflags"] = _WINDOWS_FLAGS_NO_BREAKAWAY
            proc = subprocess.Popen(args, **kwargs)
        pids[name] = proc.pid
        print(f"  dinyalakan  {name:<14} :{port}  PID {proc.pid}")

    PIDFILE.write_text(json.dumps(pids, indent=2), encoding="utf-8")

    print("\nmenunggu service siap ...")
    pending = ports(offset)
    deadline = time.time() + wait
    while pending and time.time() < deadline:
        time.sleep(2)
        for name in list(pending):
            body = health(pending[name])
            if body:
                print(f"  SIAP        {name:<14} :{pending[name]}  {describe(body)}")
                del pending[name]

    if pending:
        print()
        for name, port in pending.items():
            print(f"  GAGAL       {name:<14} :{port} tidak menjawab dalam {wait:.0f}s")
            log = LOGS / f"{name}.log"
            if log.is_file():
                tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
                for line in tail:
                    print(f"      {line}")
        return 1

    print(f"\n{len(SERVICES)} service siap. Buktikan rantainya:")
    print(f"  {python} scripts{os.sep}smoke_e2e.py              # kontrak [07] lewat orchestrator")
    print(f"  {python} local{os.sep}coba_guardrail_3.py        # ketiga guardrail paralel + gabungan")
    print("Matikan:")
    print(f"  python local{os.sep}run_local.py --stop")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stop", action="store_true", help="matikan yang sedang berjalan")
    ap.add_argument("--status", action="store_true", help="periksa saja, jangan nyalakan")
    ap.add_argument("--wait", type=float, default=60.0, help="detik menunggu health (bawaan 60)")
    ap.add_argument("--port-offset", type=int, default=0, help="geser semua port, mis. 100 -> 8130-8137")
    args = ap.parse_args()

    if args.stop:
        return cmd_stop()
    if args.status:
        return cmd_status(args.port_offset)
    return cmd_start(args.wait, args.port_offset)


if __name__ == "__main__":
    sys.exit(main())
