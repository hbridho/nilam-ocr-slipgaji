"""Menimpa setelan LLM dan prompt dari luar, tanpa menyunting config.yaml di dalam image.

    from slip_ml import runtime
    laporan = runtime.configure(
        llm={"backend": "http", "endpoint": "http://vllm.internal/v1/chat/completions"},
        prompt={"source": "file", "name": "slip_gaji", "version": 2, "path": "/etc/prompts/v2.md"},
    )

KENAPA MODUL INI ADA. Lapisan riset sudah punya jahitannya: `core.llm.CHAT_BACKENDS` memilih
penyedia dari `config.yaml` (`bedrock` / `http` / `off`), dan `core.config.PROMPT_SOURCES` memilih
dari mana prompt dibaca. Yang belum ada adalah JALAN dari environment ke sana. Tanpa itu,
"Bedrock pindah ke gateway internal" berarti menyunting config.yaml yang ikut terbungkus di dalam
image, lalu membangun ulang image — padahal yang berubah cuma satu alamat.

YANG BIKIN INI TIDAK SESEPELE "mutasi satu dict". `config.LLM` dibaca ulang setiap panggilan
(`s2_structure._ask_llm` melakukan `dict(config.LLM)`), jadi backend dan alamatnya memang hidup.
Tetapi tujuh setelan lain DIBEKUKAN jadi konstanta modul saat impor — `LLM_PROMPT`, `LLM_MODEL`,
`LLM_MAX_TOKENS`, `MIN_OCR_CHARS`, `OCR_CHAR_LIMIT`, `LLM_TEMPERATURE`, `LLM_ALL_DEFAULT` — dan
`slip_ml.structure.DEFAULT_LLM_MODEL` membekukan satu lagi di atasnya. Memutasi dict saja akan
"berhasil" tanpa suara untuk backend, dan diam-diam tidak berpengaruh untuk prompt dan model: jenis
kegagalan yang paling mahal, karena kelihatan jalan. Jadi konstanta turunannya ikut ditulis ulang,
dan `configure()` mengembalikan apa yang benar-benar berubah supaya bisa dicatat di log dan dilihat
di /health — bukan sekadar dipercaya.

PINDAH KE BASIS DATA tidak membutuhkan modul ini diubah:

    from core import config
    config.register_prompt_source("db", lambda spec, base: baca_baris(spec["name"], spec["version"]))

lalu `prompt={"source": "db", "name": "slip_gaji", "version": 3}`. `PROMPT_META` ikut dilaporkan,
sehingga sebuah hasil ekstraksi selalu bisa dilacak ke prompt yang menghasilkannya.
"""

from __future__ import annotations

from typing import Any

from slip_ml.vendor import ensure_path

ensure_path()

import s2_structure as _structure  # noqa: E402
from core import config as _config  # noqa: E402

# kunci di bagian `llm:` config.yaml -> nama konstanta di s2_structure yang membekukannya saat impor.
# Kunci yang tidak ada di sini (endpoint, api_key_env, region, entra_token_url, mantle_url,
# timeout_seconds) tidak punya konstanta beku: semuanya dibaca lewat dict(config.LLM) saat dipanggil.
_FROZEN: dict[str, str] = {
    "model": "LLM_MODEL",
    "max_tokens": "LLM_MAX_TOKENS",
    "retry_tokens": "LLM_RETRY_TOKENS",
    "min_ocr_chars": "MIN_OCR_CHARS",
    "ocr_char_limit": "OCR_CHAR_LIMIT",
    "temperature": "LLM_TEMPERATURE",
    "backend": "LLM_BACKEND",
}

PROMPT_KEYS = ("source", "name", "version", "path")


def backends() -> tuple[str, ...]:
    """Nama backend LLM yang terdaftar — dipakai untuk memeriksa setelan sebelum dipakai."""
    from core import llm as _llm

    return tuple(sorted(_llm.CHAT_BACKENDS))


def prompt_sources() -> tuple[str, ...]:
    """Sumber prompt yang terdaftar; `db` muncul di sini begitu resolvernya didaftarkan."""
    return tuple(sorted(_config.PROMPT_SOURCES))


def state() -> dict[str, Any]:
    """Setelan yang BENAR-BENAR berlaku sekarang. Dibaca dari tempat yang dipakai saat memanggil,
    bukan dari setelan yang diminta — itu bedanya laporan dengan harapan."""
    return {
        "llm_backend": _config.LLM.get("backend") or "bedrock",
        "llm_model": _structure.LLM_MODEL,
        "llm_endpoint": _config.LLM.get("endpoint"),
        "llm_region": _config.LLM.get("region"),
        "llm_all_fields": bool(_structure.LLM_ALL_DEFAULT),
        "llm_max_tokens": _structure.LLM_MAX_TOKENS,
        "prompt": dict(_config.PROMPT_META),
        "prompt_chars": len(_structure.LLM_PROMPT or ""),
        "backends_available": list(backends()),
        "prompt_sources_available": list(prompt_sources()),
    }


def configure(*, llm: dict[str, Any] | None = None, prompt: dict[str, Any] | None = None) -> dict[str, Any]:
    """Timpa setelan `llm:` dan penunjuk `prompt:`. Nilai None diabaikan (berarti "jangan ubah").

    Mengembalikan {"llm": {kunci: nilai}, "prompt": meta, "state": state()} — hanya yang berubah.
    Melempar ValueError untuk backend atau sumber prompt yang tidak terdaftar, dan untuk prompt
    kosong; FileNotFoundError untuk berkas prompt yang tidak ada: lebih baik service menolak start
    daripada berjalan dengan prompt yang bukan yang diminta, karena hasilnya tetap kelihatan masuk akal.
    """
    changed_llm: dict[str, Any] = {}

    if llm:
        bersih = {k: v for k, v in llm.items() if v is not None}
        if "backend" in bersih and bersih["backend"] not in backends():
            raise ValueError(f"backend LLM tidak dikenal: {bersih['backend']!r}; terdaftar: {', '.join(backends())}")
        if bersih.get("backend") == "http" and not (bersih.get("endpoint") or _config.LLM.get("endpoint")):
            raise ValueError("backend LLM 'http' butuh endpoint: isi LLM_ENDPOINT")

        for key, value in bersih.items():
            if _config.LLM.get(key) != value:
                changed_llm[key] = value
            # Dimutasi DI TEMPAT, bukan diganti: s2_structure memegang dict yang sama (`_LLM`),
            # dan mengikat ulang nama di core.config tidak akan terlihat dari sana.
            _config.LLM[key] = value
            if key in _FROZEN:
                setattr(_structure, _FROZEN[key], value)

        if "arbitrates" in bersih:
            _structure.LLM_ARBITRATES = tuple(bersih["arbitrates"])
        if "all_fields" in bersih:
            _structure.LLM_ALL_DEFAULT = bool(bersih["all_fields"])
        if "model" in bersih or "backend" in bersih:
            _structure.BACKEND_LABEL = {
                name: (_structure.LLM_MODEL if name == _structure.LLM_BACKEND else name)
                for name in _structure.LLM_BACKENDS
            }
            # slip_ml.structure menyalin LLM_MODEL saat impor dan memakainya sebagai cadangan
            # ketika pemanggil tidak menyebut model. Dibiarkan basi, setelan model akan diam-diam
            # tidak berpengaruh.
            from slip_ml import structure as _slip_structure

            _slip_structure.DEFAULT_LLM_MODEL = _structure.LLM_MODEL

    meta = dict(_config.PROMPT_META)
    if prompt:
        spec = {k: v for k, v in prompt.items() if k in PROMPT_KEYS and v is not None}
        if spec:
            source = spec.get("source", _config.PROMPT_META.get("source") or "file")
            if source not in prompt_sources():
                raise ValueError(
                    f"sumber prompt tidak dikenal: {source!r}; terdaftar: {', '.join(prompt_sources())}. "
                    f"Daftarkan dengan core.config.register_prompt_source({source!r}, resolver)."
                )
            spec["source"] = source
            text, meta = _config.resolve_prompt(spec, _config.CONFIG_FILE.resolve().parent)
            use_prompt(text, meta)

    return {"llm": changed_llm, "prompt": meta, "state": state()}


def current_prompt() -> tuple[str, dict[str, Any]]:
    """(teks, meta) prompt yang dipakai saat memanggil LLM sekarang."""
    return _structure.LLM_PROMPT or "", dict(_config.PROMPT_META)


def use_prompt(text: str, meta: dict[str, Any]) -> None:
    """Pasang teks prompt yang sudah dibaca, tanpa resolver — untuk prompt yang berganti saat service
    berjalan (cache Redis di service structuring). Ditulis ke tiga tempat yang sama dengan `configure`:
    `s2_structure.LLM_PROMPT` yang dipakai saat memanggil, dan `PROMPT`/`PROMPT_META` yang dilaporkan."""
    if not text.strip():
        raise ValueError(f"prompt dari {meta.get('source')!r} kosong: {meta}")
    _config.PROMPT, _config.PROMPT_META = text, dict(meta)
    _structure.LLM_PROMPT = text
