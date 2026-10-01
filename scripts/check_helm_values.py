#!/usr/bin/env python3
"""Periksa invarian values.yaml yang Kubernetes tegakkan — tanpa butuh helm terpasang.

    python scripts/check_helm_values.py [values.yaml ...]

KENAPA ADA SKRIP INI. Yang menolak nama port 18 karakter adalah API server, bukan helm: `helm
template` dan `helm lint` dua-duanya lolos, dan kesalahannya baru muncul saat apply — di tengah
deploy. Hal yang sama berlaku untuk nama variabel lingkungan ber-tanda hubung: tidak ada yang
mengeluh, setelannya hanya tidak pernah terbaca.

Yang diperiksa:

  nama port   chart memendekkannya ke 15 karakter (helper `portName`), lalu harus tetap memenuhi
              IANA_SVC_NAME: ada minimal satu huruf, hanya a-z 0-9 '-', tidak diawali/diakhiri '-',
              tanpa '--'. Juga harus unik: dua service dengan nama port sama akan bentrok di
              Service gabungan.
  nama env    `<KUNCI>_SERVICE_URL` yang dihasilkan untuk tiap upstream harus nama variabel
              lingkungan yang sah — '-' sudah diubah menjadi '_' oleh chart.
  port unik   dua service tidak boleh memakai nomor port yang sama.
  upstream    harus menunjuk kunci service yang ada, dan yang `enabled`.
  backend     tiap `*_BACKEND` di values harus nama yang benar-benar terdaftar di
              services/<nama>/app/dependencies.py. Nama yang tidak ada membuat pod crash-loop
              dengan "Unknown ... backend", dan tidak ada satu pun alat deploy yang menangkapnya
              lebih dulu. Ini bukan kemungkinan teoretis: values.yaml pernah berisi `efficientnet`,
              `paddle`, dan `slip_gaji_rules` — ketiganya sudah tidak ada di registry.
"""

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DEFAULT = REPO / "deploy" / "helm" / "nilam-ocr-slipgaji" / "values.yaml"

IANA_PORT = re.compile(r"^(?=.{1,15}$)(?!-)(?!.*--)[a-z0-9-]*[a-z][a-z0-9-]*(?<!-)$")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def port_name(key: str, svc: dict) -> str:
    """Harus sama dengan helper `nilam-ocr-slipgaji.portName`: potong 15, buang '-' di ujung."""
    return svc.get("portName") or key[:15].rstrip("-")


def url_env(key: str, svc: dict) -> str:
    """Harus sama dengan penurunan di `nilam-ocr-slipgaji.serviceEnv`."""
    return svc.get("urlEnv") or f"{key.upper().replace('-', '_')}_SERVICE_URL"


def check(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    services = data.get("services") or {}
    if not services:
        return [f"{path.name}: tidak ada bagian `services`"]

    problems: list[str] = []
    print(f"\n{path}\n{len(services)} service\n")
    print(f"{'kunci':<20} {'port':<6} {'nama port':<17} {'env upstream':<32} db  entry")
    print("-" * 92)

    seen_ports: dict[int, str] = {}
    seen_names: dict[str, str] = {}
    for key, svc in services.items():
        pname, env = port_name(key, svc), url_env(key, svc)
        db = "ya" if (svc.get("pipeline") or svc.get("database")) else "-"
        entry = "ya" if svc.get("entrypoint") else "-"
        flag = "" if IANA_PORT.match(pname) else "  <- TIDAK SAH"
        print(f"{key:<20} {svc.get('port', '?'):<6} {pname:<17} {env:<32} {db:<3} {entry}{flag}")

        if not IANA_PORT.match(pname):
            problems.append(f"{key}: nama port {pname!r} bukan IANA_SVC_NAME yang sah")
        if not ENV_NAME.match(env):
            problems.append(f"{key}: nama variabel lingkungan {env!r} tidak sah")
        if pname in seen_names:
            problems.append(f"{key}: nama port {pname!r} bentrok dengan {seen_names[pname]!r}")
        seen_names[pname] = key

        port = svc.get("port")
        if port in seen_ports:
            problems.append(f"{key}: port {port} sudah dipakai {seen_ports[port]!r}")
        seen_ports[port] = key

    for key, svc in services.items():
        for up in svc.get("upstreams") or []:
            if up not in services:
                problems.append(f"{key}: upstream {up!r} tidak ada di `services`")
            elif not services[up].get("enabled", False):
                problems.append(f"{key}: upstream {up!r} ada tetapi `enabled: false`")

    problems += check_backends(services)
    return problems


def registered_backends(key: str) -> set[str] | None:
    """Nama backend yang terdaftar di composition root sebuah service, atau None kalau berkasnya
    tidak ada (kit yang disalin tanpa services/ — bukan galat, hanya tidak bisa diperiksa)."""
    path = REPO / "services" / key / "app" / "dependencies.py"
    if not path.is_file():
        return None
    # Kunci dict registry: baris `    "nama": lambda settings: ...`.
    return set(re.findall(r'^\s+"([a-z0-9_]+)":', path.read_text(encoding="utf-8"), re.M))


def check_backends(services: dict) -> list[str]:
    problems: list[str] = []
    checked = skipped = 0
    for key, svc in services.items():
        picks = {k: v for k, v in (svc.get("env") or {}).items() if k.endswith("_BACKEND")}
        if not picks:
            continue
        names = registered_backends(key)
        if names is None:
            skipped += len(picks)
            continue
        for env_key, value in picks.items():
            checked += 1
            # "off" mematikan jalur piksel dan sengaja bukan nama backend.
            if value != "off" and value not in names:
                problems.append(f"{key}: {env_key}={value!r} tidak terdaftar; yang ada: {', '.join(sorted(names))}")
    if checked:
        print(f"\n{checked} pilihan *_BACKEND diperiksa terhadap registry service")
    if skipped:
        print(f"{skipped} tidak bisa diperiksa (services/ tidak ada di samping berkas ini)")
    return problems


def main() -> int:
    paths = [Path(a) for a in sys.argv[1:]] or [DEFAULT]
    problems: list[str] = []
    for path in paths:
        if not path.is_file():
            problems.append(f"tidak ada: {path}")
            continue
        problems += check(path)

    print()
    if problems:
        print(f"{len(problems)} MASALAH:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("semua invarian lolos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
