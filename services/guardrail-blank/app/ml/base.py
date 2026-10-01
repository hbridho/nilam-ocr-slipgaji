from typing import Any, Protocol


class BlankCheck(Protocol):
    """Pemeriksaan 1 dari 3: apakah halaman ini kosong?

    Aturan, bukan model. Tanpa teks tidak ada yang bisa dinilai — tidak mutunya, tidak
    identitasnya — jadi jawabannya harus datang lebih dulu dan tidak boleh bergantung pada apa pun
    yang dipelajari. Model yang dilatih atas halaman kosong buatan akan terlihat sempurna tanpa
    mengajari apa pun, dan akan salah pada halaman kosong yang bentuknya lain.
    """

    name: str
    metadata: dict[str, Any]

    def check(self, text: str, max_chars: int | None) -> dict[str, Any]: ...
