"""Odyssey driver exceptions — dual-base for capability + vendor reach.

Pattern: each backend-level error inherits from BOTH the vendor's
driver-level exception (`OdysseyError`) AND the capability-generic
exception (`ScanningError`, etc.). A single raise is then catchable on
either axis::

    try:
        await odyssey.scanning.start()
    except ScanningError:
        ...   # capability-generic recovery (works for any scanner backend)
    except OdysseyError:
        ...   # vendor-specific debugging

See `docs/odyssey-http-patterns.md` Pattern 3.
"""

from __future__ import annotations

from plr_v4.capabilities.scanning import ScanningError
from plr_v4.capabilities.image_retrieval import ImageRetrievalError
from plr_v4.capabilities.instrument_status import InstrumentStatusError


class OdysseyError(Exception):
    """Base exception for the LI-COR Odyssey Classic driver.

    Raised at the connection / transport layer for protocol or HTTP
    failures. Capability-specific raises use a subclass that also
    inherits from the matching capability error (see below).
    """


class OdysseyScanError(OdysseyError, ScanningError):
    """Odyssey scanning capability failed.

    Catchable as either OdysseyError (vendor-specific) or ScanningError
    (capability-generic).
    """


class OdysseyImageError(OdysseyError, ImageRetrievalError):
    """Odyssey image retrieval failed (download / list / preview)."""


class OdysseyStatusError(OdysseyError, InstrumentStatusError):
    """Odyssey status read failed."""


__all__ = [
    "OdysseyError",
    "OdysseyScanError",
    "OdysseyImageError",
    "OdysseyStatusError",
]
