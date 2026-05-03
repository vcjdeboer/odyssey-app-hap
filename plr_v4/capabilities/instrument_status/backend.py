"""Instrument status backend ABC — abstract interface for polling state.

Concrete backends implement read_status() to return an InstrumentStatusReading
from the specific instrument.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class InstrumentStatusReading:
    """Parsed instrument status."""

    state: str
    current_user: str = ""
    progress: float = 0.0
    time_remaining: str = ""
    lid_open: bool = False


class InstrumentStatusBackend(ABC):
    """Backend ABC for instrument status capability.

    Concrete backends implement:
        read_status() -> InstrumentStatusReading
    """

    @abstractmethod
    async def read_status(self) -> InstrumentStatusReading: ...

    async def setup(self) -> None:
        """Override in concrete backend if needed."""

    async def teardown(self) -> None:
        """No cleanup needed by default."""
