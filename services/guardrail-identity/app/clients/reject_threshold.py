"""Ambang identitas. Dimiliki Orkestrasi pusat, supaya bisa diubah di sana tanpa deploy di sini;
service ini membacanya dari endpoint orchestrator dan jatuh ke defaultnya sendiri bila gagal.

Arti ambangnya BERBEDA dari guardrail berbasis gambar: di sini skor model adalah P(slip gaji), dan
dokumen lolos ketika skornya >= ambang (sisi accept, sama dengan nilam-ocr-npwp). Endpoint orchestrator
menjawab `{"threshold": 0.6}`, seperti NPWP; kunci lama `{"reject_threshold": 0.6}` masih diterima dengan arti
yang sama (yang dibandingkan selalu peluang LOLOS).

Dari ketiga guardrail hanya pemeriksaan ini yang punya ambang dari luar. Batas `blank` adalah
panjang teks, bukan skor; dan skala gerbang mutu (P(halaman rusak)) berlawanan arah dengan skala di
sini. Memakai satu angka untuk ketiganya akan menggeser titik operasi dua yang lain tanpa ada yang
menyadari."""

import asyncio
import logging
import math
import time
from collections.abc import Callable
from typing import Any

from ocr_common.clients.remote import RemoteModelClient
from ocr_common.errors import ServiceError

logger = logging.getLogger(__name__)

DEFAULT_REJECT_THRESHOLD = 0.5


def default_threshold(configured: float | None, classifier: Any) -> float:
    """Ambang yang dipakai ketika orchestrator tidak memberi: IDENTITY_THRESHOLD kalau disetel, kalau
    tidak ambang yang tersimpan di checkpoint model, kalau tidak 0,5."""
    if configured is not None:
        return configured
    return float(getattr(classifier, "reject_threshold", DEFAULT_REJECT_THRESHOLD))


def parse_threshold(body: Any) -> float:
    """`{"threshold": 0.5}` (atau kunci lama `reject_threshold`) menjadi 0.5. ValueError untuk apa pun selain
    itu, termasuk nilai di luar (0, 1): 0 akan meloloskan setiap dokumen, 1 hampir tidak satu pun."""
    if not isinstance(body, dict):
        raise ValueError(f"not a JSON object: {body!r}")
    value = body["threshold"] if "threshold" in body else body.get("reject_threshold")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"no numeric threshold in {body!r}")
    if not 0 < value < 1:
        raise ValueError(f"threshold must be between 0 and 1, got {value}")
    return float(value)


class RejectThreshold:
    """Ambang yang berlaku. Dengan client (IDENTITY_THRESHOLD_URL disetel), `GET` ke endpoint
    orchestrator, disimpan `cache_seconds` supaya sebuah dokumen tidak menunggu panggilan tambahan.
    Ketika panggilan itu gagal atau menjawab sesuatu yang bukan ambang, nilai terakhir yang diberi
    orchestrator tetap berlaku (default kalau ia belum pernah memberi), dan endpointnya dicoba lagi
    setelah `cache_seconds`."""

    def __init__(
        self,
        client: RemoteModelClient | None,
        path: str,
        default: float,
        *,
        cache_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._path = path
        self.default = default
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._value: float | None = None
        self._checked_at: float | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> float:
        if self._client is None:
            return self.default
        if not self._due():
            return self._current()
        async with self._lock:
            if self._due():  # permintaan lain mungkin sudah mengambilnya sementara yang ini menunggu lock
                await self._fetch(self._client)
        return self._current()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _due(self) -> bool:
        return self._checked_at is None or self._clock() - self._checked_at >= self._cache_seconds

    def _current(self) -> float:
        return self.default if self._value is None else self._value

    async def _fetch(self, client: RemoteModelClient) -> None:
        try:
            value = parse_threshold(await client.get_json(self._path))
        except (ServiceError, ValueError) as exc:
            message = exc.message if isinstance(exc, ServiceError) else str(exc)
            logger.warning(
                "reject threshold from the orchestrator unavailable (%s); using %s", message, self._current()
            )
        else:
            if value != self._value:
                logger.info("reject threshold from the orchestrator: %s", value)
            self._value = value
        self._checked_at = self._clock()
