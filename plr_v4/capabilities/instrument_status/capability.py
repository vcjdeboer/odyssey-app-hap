"""Instrument status capability frontend — public API for polling state.

Usage (via device)::

    reading = await odyssey.status.read()
    print(reading.state)       # "Idle", "Scanning", "Paused"
    print(reading.progress)    # 0-100
    print(reading.lid_open)    # True/False
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plr_v4.capabilities.base import need_setup
from plr_v4.signals import SignalR

if TYPE_CHECKING:
    from plr_v4.capabilities.instrument_status.backend import (
        InstrumentStatusBackend,
        InstrumentStatusReading,
    )


class InstrumentStatus:
    """Instrument status capability frontend.

    Wraps an InstrumentStatusBackend with setup guards and signal emission.
    """

    status = SignalR(
        dtype="string",
        description="Instrument state",
    )

    def __init__(self, backend: InstrumentStatusBackend) -> None:
        self.backend = backend
        self._setup_finished = False

    async def setup(self) -> None:
        await self.backend.setup()
        self.status.bind_producer(self._read_status_value)

    async def stop(self) -> None:
        await self.backend.teardown()

    async def _read_status_value(self) -> str:
        result = await self.backend.read_status()
        return result.state

    @need_setup
    async def read(self) -> InstrumentStatusReading:
        result = await self.backend.read_status()
        self.status.emit(result.state)
        return result
