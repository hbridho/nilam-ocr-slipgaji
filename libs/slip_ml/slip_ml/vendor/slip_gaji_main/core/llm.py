#!/usr/bin/env python3
"""Which LLM answers, and WHERE it lives — one seam, so the host can change without code changes.

    from core import llm
    client = llm.build_client()          # backend dari config.yaml (llm.backend)
    client.chat(prompt, max_tokens=None, temperature=0.0)

Sampai sekarang s2_structure memanggil Bedrock langsung, dan tiga alamat terpaku di dalam kodenya:
titik token Entra ID, rantai STS, dan gateway chat-completions. Begitu Bedrock pindah host — ke
gateway internal, ke akun lain, ke penyedia lain sama sekali — perpindahan itu berarti menyunting
pipeline. Modul ini membalik arahnya: alamat dan backend menjadi setelan, kode tidak berubah.

MENDAFTARKAN BACKEND BARU tidak perlu menyunting modul ini:

    from core import llm
    llm.register_chat_backend("vllm", lambda cfg: MyClient(cfg["endpoint"]))

lalu `backend: vllm` di config.yaml. Pola yang sama dipakai core.config untuk sumber prompt, jadi
keduanya berperilaku sama: satu kunci di YAML, satu fungsi terdaftar.

YANG SUDAH ADA:

    bedrock   Bedrock lewat federasi Entra ID -> STS -> runtime (bawaan). Alamat Entra dan gateway
              mantle-nya sekarang bisa ditimpa lewat setelan, jadi "Bedrock di host lain" tidak
              membutuhkan backend baru sama sekali.
    http      Titik akhir apa pun yang berbicara chat-completions ala OpenAI. Inilah jalur untuk
              memindahkan beban ke server model sendiri (vLLM, TGI, LiteLLM, gateway internal).
    off       Menolak memanggil. Untuk lingkungan yang sengaja tidak boleh menembak ke luar.
"""

import json
import os
from collections.abc import Callable
from typing import Any, Protocol

from core import config


class ChatError(RuntimeError):
    """Panggilan ke model gagal, dengan pesan yang aman ditunjukkan ke pemanggil."""


class ChatClient(Protocol):
    """Yang dibutuhkan pipeline dari sebuah model. Sengaja sekecil ini: apa pun yang bisa
    mengubah satu prompt menjadi satu string balasan sudah cukup."""

    model_id: str

    def chat(self, prompt: str, max_tokens: int | None = 1024, temperature: float = 0.0) -> str: ...


# nama backend -> cara membangunnya dari bagian `llm:` config.yaml
CHAT_BACKENDS: dict[str, Callable[[dict], ChatClient]] = {}


def register_chat_backend(name: str, factory: Callable[[dict], ChatClient]) -> None:
    """Tambahkan satu backend. Dipanggil sebelum build_client()."""
    CHAT_BACKENDS[name] = factory


# ── backend: bedrock ────────────────────────────────────────────────────────────


def _bedrock(cfg: dict) -> ChatClient:
    """Bedrock lewat federasi yang sudah ada.

    Diimpor malas: boto3 dan requests hanya dibutuhkan kalau backend ini benar-benar dipakai, dan
    image yang memakai backend `http` tidak perlu memikulnya.

    Alamat yang boleh ditimpa dari config.yaml — `entra_token_url`, `mantle_url`, `region` —
    DITUMPANGKAN di atas bedrock.env, tidak menggantikannya: cfg yang diberikan ke BedrockClient
    adalah cfg lengkapnya, dan mengirim potongan akan membuang kredensial tanpa pesan.
    """
    from core import bedrock

    model = cfg.get("model")
    region = cfg.get("region")
    overrides = {key.upper(): cfg[key] for key in ("entra_token_url", "mantle_url") if cfg.get(key)}

    if not overrides and not region:
        client = bedrock.client()
        if not model or model == client.model_id:
            return client
        return bedrock.BedrockClient(model_id=model)

    env = {**bedrock.load_env(), **overrides}
    return bedrock.BedrockClient(cfg=env, region=region, model_id=model)


# ── backend: http ───────────────────────────────────────────────────────────────


class HttpChatClient:
    """Titik akhir chat-completions ala OpenAI, di host mana pun.

    Bentuk permintaan dan balasannya adalah yang dipakai OpenAI, vLLM, TGI, Ollama (mode
    kompatibel) dan sebagian besar gateway internal — jadi memindahkan beban ke server sendiri
    hanya soal mengisi `endpoint`.
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        extra_headers: dict | None = None,
    ):
        if not endpoint:
            raise ChatError("llm.endpoint kosong: backend 'http' butuh alamat titik akhir")
        self.endpoint = endpoint
        self.model_id = model or ""
        self.timeout = timeout
        self._headers = {"Content-Type": "application/json", **(extra_headers or {})}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def chat(self, prompt: str, max_tokens: int | None = 1024, temperature: float = 0.0) -> str:
        import requests

        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        # max_tokens None berarti "tanpa batas" — kuncinya DIHILANGKAN, bukan dikirim null.
        # Mengirim null ditolak sebagian gateway, dan mengirim 0 berarti sesuatu yang lain lagi.
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        try:
            resp = requests.post(self.endpoint, headers=self._headers, timeout=self.timeout, data=json.dumps(body))
        except Exception as exc:  # jaringan, DNS, TLS
            raise ChatError(f"{self.endpoint} tidak bisa dihubungi: {exc}") from exc
        if resp.status_code != 200:
            raise ChatError(f"{self.endpoint} menjawab {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ChatError(f"{self.endpoint} menjawab dalam bentuk yang tidak dikenali") from exc


def _http(cfg: dict) -> ChatClient:
    key_env = cfg.get("api_key_env")
    return HttpChatClient(
        endpoint=cfg.get("endpoint", ""),
        model=cfg.get("model", ""),
        api_key=os.environ.get(key_env) if key_env else None,
        timeout=float(cfg.get("timeout_seconds", 120.0)),
        extra_headers=cfg.get("headers") or None,
    )


# ── backend: off ────────────────────────────────────────────────────────────────


class DisabledChatClient:
    """Menolak memanggil, dengan alasan yang menyebut cara menyalakannya kembali."""

    model_id = "off"

    def chat(self, prompt: str, max_tokens: int | None = 1024, temperature: float = 0.0) -> str:
        raise ChatError("LLM dimatikan (llm.backend: off di config.yaml)")


register_chat_backend("bedrock", _bedrock)
register_chat_backend("http", _http)
register_chat_backend("off", lambda cfg: DisabledChatClient())


# ── pilih ───────────────────────────────────────────────────────────────────────


def build_client(cfg: dict | None = None) -> ChatClient:
    """Klien untuk backend yang disebut config.yaml (`llm.backend`, bawaan `bedrock`)."""
    cfg = config.LLM if cfg is None else cfg
    name = cfg.get("backend") or "bedrock"
    factory = CHAT_BACKENDS.get(name)
    if factory is None:
        known = ", ".join(sorted(CHAT_BACKENDS))
        raise ChatError(f"backend LLM tidak dikenal: {name!r}; terdaftar: {known}")
    return factory(cfg)
