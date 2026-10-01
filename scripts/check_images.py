#!/usr/bin/env python3
"""Periksa apa yang bisa diperiksa dari image TANPA menjalankan docker.

    python scripts/check_images.py [nama-service ...]

Ini bukan pengganti `docker build`. Yang diuji di sini dua hal yang paling sering salah dan paling
mahal kalau baru ketahuan di CI atau di cluster:

  A · ISI BUILD CONTEXT. Tiap Dockerfile memasang pustaka lokal dengan `pip install ./libs/<nama>`.
      `.dockerignore` membuang `**/pyproject.toml`, jadi tiap pustaka yang dipasang HARUS punya
      baris pengecualian `!libs/<nama>/pyproject.toml`. Tanpa itu pip berhenti dengan "Neither
      'setup.py' nor 'pyproject.toml' found" — dan itu hanya terlihat saat build.

  B · IMPOR vs LOCK. Tiap service memasang dependensinya dari requirements.lock, lalu
      ocr_common/slip_ml dipasang `--no-deps`. Artinya dependensi PUSTAKA tidak ikut terbawa: kalau
      kode yang dijangkau service mengimpor numpy sementara lock-nya tidak memuat numpy, image-nya
      jadi tanpa suara, dan gagalnya baru muncul saat permintaan pertama.

      Grafik impornya ditelusuri sungguhan dari `app/` — masuk ke slip_ml, ke core/, ke modul yang
      di-vendor — bukan sekadar membaca requirements.txt. Impor di tingkat modul dibedakan dari
      impor di dalam fungsi: yang pertama membuat service gagal START, yang kedua baru gagal ketika
      jalur itu dipakai (mis. boto3 hanya saat ENABLE_LLM dan backend bedrock).

  C · CLOUD BUILD, TANPA LOGIN. `gcloud builds submit` baru memvalidasi apa pun setelah context
      diunggah, dan untuk itu ia butuh kredensial. Yang di bawah ini bisa diperiksa tanpa login:
      tiap langkah menunjuk Dockerfile yang ada, tag-nya konsisten dengan `images:`, tiap substitusi
      yang dipakai sudah dideklarasikan, ketujuh service punya langkahnya, dan — yang paling
      mahal kalau salah — BERAPA BESAR yang akan diunggah setelah `.gcloudignore` diterapkan.

Keterbatasan yang harus diingat: pencocok `.dockerignore`/`.gcloudignore` di sini bersasaran, bukan
implementasi penuh aturan docker/gcloud, dan versi Python di sini bukan 3.11 seperti di image.
"""

import ast
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# modul yang diimpor -> nama distribusi di requirements.lock
DIST = {
    "numpy": "numpy",
    "fitz": "pymupdf",
    "pymupdf": "pymupdf",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "requests": "requests",
    "httpx": "httpx",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "starlette": "starlette",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",
    "multipart": "python-multipart",
    "sqlalchemy": "sqlalchemy",
    "greenlet": "greenlet",
    "asyncpg": "asyncpg",
    "openpyxl": "openpyxl",
    "rapidfuzz": "rapidfuzz",
    "sklearn": "scikit-learn",
    "joblib": "joblib",
    "boto3": "boto3",
    "botocore": "boto3",
    "cv2": "opencv-python-headless",
    "pypdfium2": "pypdfium2",
    "rapidocr_onnxruntime": "rapidocr-onnxruntime",
    "onnxruntime": "onnxruntime",
    "prometheus_client": "prometheus-client",
    "alembic": "alembic",
}

# Akar paket yang ada DI DALAM repo: ditelusuri, bukan dianggap dependensi pihak ketiga.
LOCAL_ROOTS = {
    "app": None,  # diselesaikan relatif terhadap service yang sedang diperiksa
    "ocr_common": REPO / "libs" / "ocr_common",
    "slip_ml": REPO / "libs" / "slip_ml",
    "tests": None,
}
# Modul di dalam vendor yang diimpor tanpa awalan paket (sys.path diatur `ensure_path`).
VENDOR = REPO / "libs" / "slip_ml" / "slip_ml" / "vendor" / "slip_gaji_main"


def ignore_rules(name: str) -> tuple[list[str], list[str]]:
    path = REPO / name
    if not path.is_file():
        return [], []
    lines = [
        x.strip() for x in path.read_text(encoding="utf-8").splitlines() if x.strip() and not x.strip().startswith("#")
    ]
    return [x for x in lines if not x.startswith("!")], [x[1:] for x in lines if x.startswith("!")]


def ignored(rel: str, patterns: list[str], negations: list[str]) -> bool:
    """Cocok bersasaran, cukup untuk pola di berkas ini: nama biasa, `**/x`, dan `x*`."""

    def hit(path: str, pattern: str) -> bool:
        if pattern.startswith("**/"):
            rest = pattern[3:]
            return (
                fnmatch(Path(path).name, rest)
                or fnmatch(path, rest)
                or any(fnmatch("/".join(Path(path).parts[i:]), rest) for i in range(len(Path(path).parts)))
            )
        return path == pattern or path.startswith(pattern + "/") or fnmatch(path, pattern)

    if any(hit(rel, n) for n in negations):
        return False
    return any(hit(rel, p) for p in patterns)


def imports_of(path: Path) -> tuple[set[str], set[str]]:
    """(impor tingkat modul, impor di dalam fungsi) sebagai nama BERTITIK LENGKAP.

    Lengkap, bukan akarnya saja: `from slip_ml import guard` harus menunjuk slip_ml/guard.py, bukan
    seluruh paket slip_ml. Memakai akarnya membuat satu impor menarik tiap modul paket itu — dan
    dengan itu seluruh dependensi opsionalnya, yang tampak sebagai kekurangan yang tidak ada.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    top: set[str] = set()
    deep: set[str] = set()

    def names(node: ast.AST) -> set[str]:
        out: set[str] = set()
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module)
            # `from pkg import sub` bisa berarti submodul, bisa juga nama di dalam __init__.
            # Keduanya dicoba; yang bukan berkas akan gagal diselesaikan dan diabaikan.
            out |= {f"{node.module}.{a.name}" for a in node.names}
        return out

    for node in tree.body:
        top |= names(node)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for inner in ast.walk(node):
                deep |= names(inner)
    return top, deep - top


def resolve(dotted: str, service_root: Path) -> list[Path]:
    """Berkas di dalam repo untuk satu nama modul bertitik, atau [] kalau pihak ketiga/stdlib.

    Paket induknya ikut: mengimpor `a.b.c` menjalankan `a/__init__.py` dan `a/b/__init__.py`.
    """
    parts = dotted.split(".")
    for base in (service_root, LOCAL_ROOTS["ocr_common"], LOCAL_ROOTS["slip_ml"], VENDOR):
        if base is None:
            continue
        module = base.joinpath(*parts)
        target = None
        if module.with_suffix(".py").is_file():
            target = module.with_suffix(".py")
        elif (module / "__init__.py").is_file():
            target = module / "__init__.py"
        if target is None:
            continue
        out = [target]
        for depth in range(1, len(parts)):
            init = base.joinpath(*parts[:depth]) / "__init__.py"
            if init.is_file():
                out.append(init)
        return out
    return []


def walk_imports(service: str) -> tuple[set[str], set[str]]:
    """Semua distribusi pihak ketiga yang dijangkau service ini: (wajib, saat dipakai)."""
    root = REPO / "services" / service
    seen: set[str] = set()
    wajib: set[str] = set()
    nanti: set[str] = set()
    queue: list[tuple[Path, bool]] = [(p, True) for p in sorted((root / "app").rglob("*.py"))]

    while queue:
        path, hard = queue.pop()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            top, deep = imports_of(path)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for module, is_top in [(m, True) for m in top] + [(m, False) for m in deep]:
            files = resolve(module, root)
            if files:
                queue += [(f, hard and is_top) for f in files]
                continue
            # Pihak ketiga: yang menentukan paketnya nama AKARNYA (`PIL.Image` -> pillow).
            dist = DIST.get(module.split(".")[0])
            if dist is None:
                continue  # stdlib atau tidak dikenal; bukan urusan pemeriksaan ini
            (wajib if (hard and is_top) else nanti).add(dist)
    return wajib, nanti - wajib


def locked(service: str) -> set[str]:
    text = (REPO / "services" / service / "requirements.lock").read_text(encoding="utf-8")
    return set(re.findall(r"(?m)^([a-z0-9][a-z0-9._-]*)==", text))


def check_cloudbuild(all_services: list[str], gcloud_rules: tuple[list[str], list[str]]) -> list[str]:
    import yaml

    path = REPO / "deploy" / "gke" / "cloudbuild.yaml"
    if not path.is_file():
        print(f"  (tidak ada {path.relative_to(REPO).as_posix()} — dilewati)")
        return []

    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    subs = config.get("substitutions") or {}
    steps = config.get("steps") or []
    images = set(config.get("images") or [])
    problems: list[str] = []

    expected_images = set()
    for step in steps:
        name = step.get("id", "?")
        args = step.get("args") or []
        dockerfile = args[args.index("-f") + 1] if "-f" in args else None
        tag = args[args.index("-t") + 1] if "-t" in args else None
        want_tag = "${_REGISTRY}/${_PREFIX}-" + f"{name}:" + "${_TAG}"
        ada = dockerfile is not None and (REPO / dockerfile).is_file()
        cocok = tag == want_tag
        print(f"  {'ok  ' if ada and cocok else 'GAGAL'} {name:<20} {dockerfile}")
        if not ada:
            problems.append(f"cloudbuild: langkah {name}: Dockerfile {dockerfile!r} tidak ada")
        if not cocok:
            problems.append(f"cloudbuild: langkah {name}: tag {tag!r} tidak sama dengan {want_tag!r}")
        if tag:
            expected_images.add(tag)
        # Substitusi yang dipakai harus dideklarasikan; yang tidak akan terkirim sebagai teks apa adanya.
        for used in re.findall(r"\$\{(_[A-Z_]+)\}", " ".join(args)):
            if used not in subs:
                problems.append(f"cloudbuild: langkah {name}: substitusi {used} tidak dideklarasikan")

    for extra in sorted(images - expected_images):
        problems.append(f"cloudbuild: `images:` memuat {extra} yang tidak dibangun langkah mana pun")
    for missing in sorted(expected_images - images):
        problems.append(f"cloudbuild: {missing} dibangun tetapi tidak ada di `images:` — tidak akan di-push")

    ids = [s.get("id") for s in steps]
    for service in all_services:
        if service not in ids:
            problems.append(f"cloudbuild: service {service!r} tidak punya langkah build")

    # Berapa yang benar-benar diunggah. Tanpa .gcloudignore ini 463 MB, hampir semuanya .venv.
    patterns, negations = gcloud_rules
    total = count = 0
    for file in REPO.rglob("*"):
        if not file.is_file():
            continue
        rel = file.relative_to(REPO).as_posix()
        if patterns and ignored(rel, patterns, negations):
            continue
        count += 1
        total += file.stat().st_size
    print(f"\n  akan diunggah: {count} berkas, {total / 1024 / 1024:.1f} MB")
    if total > 50 * 1024 * 1024:
        problems.append(f"cloudbuild: context {total / 1024 / 1024:.0f} MB — periksa .gcloudignore")
    return problems


def main() -> int:
    wanted = sys.argv[1:] or sorted(p.name for p in (REPO / "services").iterdir() if p.is_dir())
    problems: list[str] = []

    # Dua saringan, berurutan: .gcloudignore menentukan apa yang DIUNGGAH ke Cloud Build, lalu
    # .dockerignore menentukan apa yang masuk build context. Yang dibuang saringan pertama tidak
    # bisa dikembalikan saringan kedua — jadi keduanya harus meloloskan pyproject.toml.
    filters = {name: ignore_rules(name) for name in (".gcloudignore", ".dockerignore")}

    print("A · ISI BUILD CONTEXT\n")
    for service in wanted:
        dockerfile = (REPO / "services" / service / "Dockerfile").read_text(encoding="utf-8")
        libs = sorted(set(re.findall(r"^COPY (libs/[a-z_]+) ", dockerfile, re.M)))
        installed = set(re.findall(r"\./(libs/[a-z_]+)", dockerfile))
        for lib in libs:
            needs_pyproject = lib in installed
            rel = f"{lib}/pyproject.toml"
            blockers = [
                name
                for name, (patterns, negations) in filters.items()
                if patterns and ignored(rel, patterns, negations)
            ]
            bad = needs_pyproject and blockers
            note = "dipasang pip" if needs_pyproject else "hanya disalin"
            print(f"  {'GAGAL' if bad else 'ok  '} {service:<20} {lib:<18} {note:<14} pyproject lolos: {not blockers}")
            if bad:
                problems.append(
                    f"{service}: {rel} dibuang {', '.join(blockers)} padahal `pip install ./{lib}` "
                    f"membutuhkannya — tambahkan `!{rel}`"
                )

    print("\nB · IMPOR vs requirements.lock\n")
    for service in wanted:
        have = locked(service)
        wajib, nanti = walk_imports(service)
        kurang = sorted(wajib - have)
        kurang_nanti = sorted(nanti - have)
        status = "GAGAL" if kurang else ("catat" if kurang_nanti else "ok   ")
        print(f"  {status} {service:<20} {len(wajib)} wajib, {len(nanti)} saat dipakai")
        if kurang:
            print(f"        KURANG (gagal START): {', '.join(kurang)}")
            problems.append(f"{service}: impor tingkat modul tanpa paketnya di lock: {', '.join(kurang)}")
        if kurang_nanti:
            print(f"        tidak di lock, hanya dibutuhkan saat jalurnya dipakai: {', '.join(kurang_nanti)}")

    print("\nC · CLOUD BUILD (tanpa login gcloud)\n")
    all_services = sorted(p.name for p in (REPO / "services").iterdir() if p.is_dir())
    problems += check_cloudbuild(all_services, filters[".gcloudignore"])

    print()
    if problems:
        print(f"{len(problems)} MASALAH:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("lolos — tetap jalankan `deploy.sh all --build-only` untuk membuktikan build-nya")
    return 0


if __name__ == "__main__":
    sys.exit(main())
