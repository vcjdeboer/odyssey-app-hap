from plr_v4.capabilities.instrument_status.backend import (
    InstrumentStatusBackend,
    InstrumentStatusReading,
)
from plr_v4.capabilities.instrument_status.capability import InstrumentStatus


class InstrumentStatusError(Exception):
    """Capability-generic exception for instrument-status read failures."""


__all__ = [
    "InstrumentStatusBackend", "InstrumentStatusReading",
    "InstrumentStatus", "InstrumentStatusError",
]
