"""Lightweight Device base class with lifecycle management.

Standalone — no PLR dependency. Provides:
- setup()/stop() that propagate to registered drivers
- setup_finished property
- Async context manager (async with)

Devices register their drivers (capability instances) so the Device
can manage their lifecycle in the correct order.
"""

from __future__ import annotations

import logging
from typing import Optional

from plr_v4.device_card import DeviceCard

logger = logging.getLogger(__name__)


class Device:
    """Base device class — owns drivers and manages their lifecycle."""

    def __init__(self, card: Optional[DeviceCard] = None) -> None:
        self.card = card
        self._drivers: list = []
        self._setup_finished = False

    @property
    def setup_finished(self) -> bool:
        return self._setup_finished

    async def setup(self) -> None:
        """Set up all registered drivers in order."""
        for driver in self._drivers:
            await driver.setup()
            driver._setup_finished = True
        self._setup_finished = True

    async def stop(self) -> None:
        """Stop all registered drivers in reverse order."""
        for driver in reversed(self._drivers):
            try:
                await driver.stop()
            except Exception:
                logger.exception("Error stopping %s", type(driver).__name__)
            driver._setup_finished = False
        self._setup_finished = False

    async def __aenter__(self):
        await self.setup()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
        return False
