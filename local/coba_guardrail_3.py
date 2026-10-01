#!/usr/bin/env python3
"""Ketiga guardrail sebagai tiga service, dipanggil PARALEL, lalu digabung jadi satu putusan.

    python local/coba_guardrail_3.py [BERKAS.pdf]
    python local/coba_guardrail_3.py --teks "SLIP GAJI ..."     # tanpa OCR, teks langsung

Yang dikerjakan:

    POST :8030/v1/extraction/extract        berkas -> teks OCR + ringkasan skor
    lalu bersamaan, satu payload yang sama ke ketiganya:
      POST :8035/v1/guardrail/blank/check      kosong?      aturan panjang teks
      POST :8036/v1/guardrail/blur/check       terbaca?     6 ciri mutu OCR
      POST :8037/v1/guardrail/identity/check   slip gaji?   TF-IDF + tata letak
    lalu digabung: kosong > buram > identitas (`slip_ml.guard.combine`)

Kenapa skrip ini ada. Tiga service yang berdiri sendiri kehilangan hubungan pendek kaskadenya:
pemeriksaan mutu dan identitas tetap menjawab meski halamannya kosong, dan jawaban mereka di situ
tidak berarti apa-apa. Yang mendahulukan `blank` sekarang penggabung, bukan urutan pemanggilan.
Skrip ini memperlihatkan keduanya sekaligus — tiga jawaban mentah, lalu putusan gabungannya —
supaya kalau hasil akhirnya tidak seperti harapan, kelihatan pemeriksaan mana yang keliru dan
apakah yang keliru penggabungnya.

Waktu tiap panggilan ikut dicetak: itu yang membuat "paralel" bisa dibuktikan, bukan diklaim.
"""

import argparse
import asyncio
import sys
import time
from functools import lru_cache
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "libs" / "slip_ml"))

CHECKS = {
    "blank": (8035, "/v1/guardrail/blank/check", "guardrail-blank"),
    "blur": (8036, "/v1/guardrail/blur/check", "guardrail-blur"),
    "identity": (8037, "/v1/guardrail/identity/check", "guardrail-identity"),
}


@lru_cache
def key_of(service: str) -> str:
    """Di-cache: tanpa itu setiap panggilan membaca .env dari disk, di dalam bagian yang diukur."""
    path = REPO / "services" / service / ".env"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"API_KEY tidak ada di {path}")


def pick(given: str | None) -> Path:
    if given:
        path = Path(given)
        if not path.is_file():
            raise SystemExit(f"berkas tidak ada: {path}")
        return path
    folder = REPO.parent / "reference"
    for path in sorted(folder.glob("*_slip_gaji.pdf")):
        return path
    raise SystemExit(f"tidak menemukan slip gaji di {folder}")


async def ask(client: httpx.AsyncClient, name: str, payload: dict) -> tuple[str, dict | None, float, str]:
    port, path, service = CHECKS[name]
    started = time.perf_counter()
    try:
        response = await client.post(
            f"http://127.0.0.1:{port}{path}", headers={"X-API-Key": key_of(service)}, json=payload
        )
    except httpx.HTTPError as exc:
        return name, None, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"
    took = time.perf_counter() - started
    if response.status_code >= 400:
        body = response.json()
        return name, None, took, f"HTTP {response.status_code} {body.get('errors')}: {body.get('message')}"
    return name, response.json()["data"], took, ""


async def run(text: str, confidence: dict | None) -> int:
    from slip_ml import guard

    payload = {"text": text, "confidence": confidence}
    print(
        f"teks   : {len(text)} karakter"
        + (f"  ·  skor OCR rata-rata {confidence.get('mean')}" if confidence else "  ·  tanpa ringkasan skor OCR")
    )

    print("\nTIGA PEMERIKSAAN, PARALEL")
    async with httpx.AsyncClient(timeout=60) as client:
        # Satu putaran pemanasan yang tidak diukur. Tanpa itu jam dinding memuat pembuatan koneksi
        # TCP ke tiga port — ratusan milidetik yang tidak ada hubungannya dengan paralel atau tidak,
        # dan yang membuat pengukurannya berbohong ke arah "tidak paralel".
        await asyncio.gather(*(ask(client, name, payload) for name in CHECKS))

        wall = time.perf_counter()
        results = await asyncio.gather(*(ask(client, name, payload) for name in CHECKS))
        wall = time.perf_counter() - wall

        # Pembanding: ketiganya berurutan, lewat koneksi yang sama yang sudah hangat.
        serial = time.perf_counter()
        for name in CHECKS:
            await ask(client, name, payload)
        serial = time.perf_counter() - serial

    reports: dict[str, dict | None] = {}
    total = 0.0
    for name, data, took, problem in results:
        reports[name] = data
        total += took
        if data is None:
            print(f"  {name:<9} {took * 1000:6.0f} ms  TIDAK MENJAWAB — {problem}")
            continue
        mark = "lolos " if data["passed"] else "TAHAN "
        print(f"  {name:<9} {took * 1000:6.0f} ms  {mark} {data['verdict']}")
        for field in ("chars", "max_chars", "p_broken", "threshold", "blank", "proba_slip_gaji", "reject_threshold"):
            if field in data:
                print(f"              {field:<18} {data[field]}")

    # Kalau ketiganya benar-benar berjalan bersamaan, jam dinding mendekati yang paling lambat,
    # bukan jumlah ketiganya — dan lebih pendek daripada memanggilnya berurutan.
    slowest = max(took for _, _, took, _ in results)
    print(
        f"\n  paralel  {wall * 1000:6.1f} ms jam dinding (terlambat sendiri {slowest * 1000:.1f} ms,"
        f" jumlah ketiganya {total * 1000:.1f} ms)"
    )
    verdict = f"paralel: hemat {(serial - wall) * 1000:.1f} ms" if wall < serial * 0.8 else "TIDAK terlihat paralel"
    print(f"  berurutan{serial * 1000:6.1f} ms  ->  {verdict}")

    print("\nGABUNGAN (kosong > buram > identitas)")
    combined = guard.combine(reports)
    print(f"  verdict    : {combined['verdict']}")
    print(f"  passed     : {combined['passed']}")
    print(f"  reason     : {combined['reason']}")
    if combined["unavailable"]:
        print(
            f"  tidak menjawab: {', '.join(combined['unavailable'])}"
            "  <- penjaga yang bisu bukan izin, jadi dokumen ini belum diputuskan"
        )

    if reports["identity"] is not None and reports["blank"] is not None:
        cascade = guard.check(text, confidence)
        sama = (cascade["verdict"] == combined["verdict"]) and (cascade["passed"] == combined["passed"])
        print(f"\n  kaskade satu proses: {cascade['verdict']}  ->  {'SAMA' if sama else 'BEDA'}")
        if not sama:
            print("  BEDA berarti ada yang salah di penggabung atau di salah satu service.")
            return 1

    return 0 if combined["passed"] or combined["verdict"] != "guardrail_tidak_lengkap" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("document", nargs="?")
    ap.add_argument("--teks", help="lewati OCR, nilai teks ini langsung")
    args = ap.parse_args()

    if args.teks is not None:
        return asyncio.run(run(args.teks, None))

    doc = pick(args.document)
    print(f"berkas : {doc.name}  ({doc.stat().st_size / 1024:.0f} KB)")
    print("\n1 · EXTRACTION  POST :8030/v1/extraction/extract")
    with httpx.Client(timeout=300) as client:
        response = client.post(
            "http://127.0.0.1:8030/v1/extraction/extract",
            headers={"X-API-Key": key_of("extraction")},
            files={"file": (doc.name, doc.read_bytes(), "application/pdf")},
        )
    if response.status_code >= 400:
        body = response.json()
        print(f"   HTTP {response.status_code} {body.get('errors')}: {body.get('message')}")
        return 1
    data = response.json()["data"]
    pages = data.get("pages") or []
    print(f"   HTTP 200  OK · {len(pages)} halaman · {len(data.get('full_text') or '')} karakter")

    # Ringkasan skor halaman pertama: itulah bentuk yang diharapkan gerbang mutu.
    confidence = (pages[0].get("confidence") if pages else None) or None
    return asyncio.run(run(data.get("full_text") or "", confidence))


if __name__ == "__main__":
    sys.exit(main())
