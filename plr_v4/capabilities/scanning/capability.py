"""Scanning capability frontend — public API for scan control.

Usage (via device)::

    await odyssey.scanning.configure(params)
    await odyssey.scanning.start()
    await odyssey.scanning.stop()    # saves files
    await odyssey.scanning.cancel()  # aborts without saving
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from plr_v4.capabilities.base import need_setup
from plr_v4.signals import SignalX

if TYPE_CHECKING:
    from plr_v4.capabilities.scanning.backend import ScanningBackend


class Scanning:
    """Scanning capability frontend.

    Wraps a ScanningBackend with setup guards and signal emission.
    """

    scan_action = SignalX(description="Scan state change")

    def __init__(self, backend: ScanningBackend) -> None:
        self.backend = backend
        self._setup_finished = False

    async def setup(self) -> None:
        await self.backend.setup()

    async def stop(self) -> None:
        """Lifecycle stop — safety cancel on teardown."""
        await self.backend.teardown()

    @need_setup
    async def configure(self, params: Any) -> None:
        await self.backend.configure(params)
        self.scan_action.emit(payload={"action": "configure"})

    @need_setup
    async def start(self) -> None:
        await self.backend.start()
        self.scan_action.emit(payload={"action": "start"})

    @need_setup
    async def stop_scan(self) -> None:
        """Stop scanning (saves files)."""
        await self.backend.stop()
        self.scan_action.emit(payload={"action": "stop"})

    @need_setup
    async def pause(self) -> None:
        await self.backend.pause()
        self.scan_action.emit(payload={"action": "pause"})

    @need_setup
    async def cancel(self) -> None:
        await self.backend.cancel()
        self.scan_action.emit(payload={"action": "cancel"})
