"""LI-COR Odyssey Classic driver — public API.

Import the common names directly from this package::

    from plr_v4.odyssey import OdysseyClassic, ScanParameters, DEFAULT_GROUP
"""

from plr_v4.odyssey.connection import (
    OdysseyDriver,
    ScanParameters,
    DEFAULT_GROUP,
)
from plr_v4.odyssey.device import OdysseyClassic
from plr_v4.odyssey.device_card import ODYSSEY_CLASSIC_BASE
from plr_v4.odyssey.errors import (
    OdysseyError,
    OdysseyScanError,
    OdysseyImageError,
    OdysseyStatusError,
)
from plr_v4.odyssey.scan_backend import StopResult
from plr_v4.odyssey.status_backend import InstrumentState, normalize_state
from plr_v4.odyssey.tagging import (
    build_identity_description,
    tag_tiff_with_identity,
    write_hyperstack,
    DEFAULT_SOFTWARE_TAG,
    PRIVATE_JSON_TAG,
)

__all__ = [
    "OdysseyClassic",
    "OdysseyDriver",
    "ScanParameters",
    "DEFAULT_GROUP",
    "ODYSSEY_CLASSIC_BASE",
    "OdysseyError",
    "OdysseyScanError",
    "OdysseyImageError",
    "OdysseyStatusError",
    "StopResult",
    "InstrumentState",
    "normalize_state",
    "build_identity_description",
    "tag_tiff_with_identity",
    "write_hyperstack",
    "DEFAULT_SOFTWARE_TAG",
    "PRIVATE_JSON_TAG",
]
