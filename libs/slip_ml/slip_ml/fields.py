"""Nama field slip gaji — satu sumber untuk kelima service.

Diambil dari pipeline yang di-vendor, bukan diketik ulang: daftar yang diketik ulang akan
bergeser diam-diam begitu pipeline menambah field.
"""

from slip_ml.vendor import ensure_path

ensure_path()

from core.fields import (  # noqa: E402
    ALL_FIELDS as _ALL,
)
from core.fields import (
    DISPLAY as _DISPLAY,
)
from core.fields import (
    EARNINGS as _EARNINGS,
)
from core.fields import (
    IDENTITY as _IDENTITY,
)
from core.fields import (
    MANDATORY as _MANDATORY,
)
from core.fields import (
    MONEY_FIELDS as _MONEY,
)
from core.fields import (
    TOTALS as _TOTALS,
)
from core.fields import (
    blank,
)

ALL_FIELDS: tuple[str, ...] = tuple(_ALL)
IDENTITY_FIELDS: tuple[str, ...] = tuple(_IDENTITY)
EARNINGS_FIELDS: tuple[str, ...] = tuple(_EARNINGS)
TOTAL_FIELDS: tuple[str, ...] = tuple(_TOTALS)
MONEY_FIELDS: frozenset[str] = frozenset(_MONEY)
MANDATORY_FIELDS: tuple[str, ...] = tuple(_MANDATORY)
DISPLAY: dict[str, str] = dict(_DISPLAY)

__all__ = [
    "ALL_FIELDS",
    "DISPLAY",
    "EARNINGS_FIELDS",
    "IDENTITY_FIELDS",
    "MANDATORY_FIELDS",
    "MONEY_FIELDS",
    "TOTAL_FIELDS",
    "blank",
    "missing_mandatory",
]


def missing_mandatory(values: dict[str, object]) -> list[str]:
    """Field wajib yang tidak terisi pada satu slip, menurut urutan MANDATORY_FIELDS."""
    return [f for f in MANDATORY_FIELDS if blank(values.get(f))]
