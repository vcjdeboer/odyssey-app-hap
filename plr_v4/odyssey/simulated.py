"""Simulated Odyssey backends for testing without hardware.

Provides simulated versions of all three Odyssey backends
that maintain internal state for realistic test behavior.
No HTTP connection needed — everything runs in memory.

State values follow the canonical InstrumentState literal defined in
status_backend.py, so the simulated path is behaviourally identical to
the real path (FR-029, simulator parity).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from plr_v4.capabilities.scanning import ScanningBackend
from plr_v4.capabilities.image_retrieval import ImageRetrievalBackend
from plr_v4.capabilities.instrument_status import (
    InstrumentStatusBackend,
    InstrumentStatusReading,
)
from plr_v4.odyssey.connection import DEFAULT_GROUP
from plr_v4.odyssey.status_backend import InstrumentState

logger = logging.getLogger(__name__)


class OdysseyState:
    """Shared mutable state for simulated Odyssey backends.

    ``scanner_state`` values match the canonical InstrumentState literal
    (see status_backend.py). ``stop_was_partial`` records whether the
    most recent Stop transition saved a partial image (see FR-021).
    """

    def __init__(self) -> None:
        self.scanner_state: InstrumentState = "Idle"
        self.progress: float = 0.0
        self.lid_open: bool = False
        self.current_user: str = ""
        self.current_scan_name: str = ""
        self.current_group: str = ""
        self.configured: bool = False
        self.stop_was_partial: bool = False
        # Pre-seed the default working group so the simulator mirrors
        # the lab instrument (FR-026).
        self.scans: dict[str, dict[str, bytes]] = {
            DEFAULT_GROUP: {
                "test_scan": b"SIMULATED_TIFF_DATA_700nm",
            },
        }


class OdysseyScanSimulated(ScanningBackend):
    """Simulated scanning backend for Odyssey."""

    def __init__(self, state: OdysseyState) -> None:
        self._state = state

    async def configure(self, params: Any) -> None:
        self._state.configured = True
        self._state.current_scan_name = getattr(params, "name", "scan")
        self._state.current_group = getattr(params, "group", DEFAULT_GROUP)
        self._state.scanner_state = "Configured"
        self._state.stop_was_partial = False
        logger.info("Configured scan: %s", self._state.current_scan_name)

    async def start(self) -> None:
        if not self._state.configured:
            raise RuntimeError("Scan not configured")
        self._state.scanner_state = "Scanning"
        self._state.progress = 0.0
        for i in range(0, 101, 10):
            # Allow stop() to interrupt by checking state each tick.
            if self._state.scanner_state != "Scanning":
                return
            self._state.progress = float(i)
            await asyncio.sleep(0.05)
        self._state.scanner_state = "Completed"
        self._state.progress = 100.0
        self._save_scan(partial=False)
        self._state.configured = False

    async def stop(self) -> None:
        """Graceful abort (FR-021): save whatever has been acquired.

        Mirrors OdysseyScanBackend.stop_and_save() — write a partial TIFF
        and transition to Stopped. Next configure/start resets state.
        """
        if self._state.scanner_state == "Scanning":
            self._save_scan(partial=True)
            self._state.stop_was_partial = True
        self._state.scanner_state = "Stopped"
        self._state.configured = False

    async def pause(self) -> None:
        self._state.scanner_state = "Paused"

    async def cancel(self) -> None:
        # FR-021 distinguishes Cancel from Stop: Cancel discards.
        self._state.scanner_state = "Idle"
        self._state.progress = 0.0
        self._state.configured = False
        self._state.stop_was_partial = False

    def _save_scan(self, partial: bool) -> None:
        group = self._state.current_group or DEFAULT_GROUP
        name = self._state.current_scan_name or "scan"
        if group not in self._state.scans:
            self._state.scans[group] = {}
        payload = (
            b"SIMULATED_PARTIAL_TIFF_DATA" if partial
            else b"SIMULATED_TIFF_DATA"
        )
        self._state.scans[group][name] = payload


class OdysseyImageSimulated(ImageRetrievalBackend):
    """Simulated image retrieval backend for Odyssey."""

    def __init__(self, state: OdysseyState) -> None:
        self._state = state

    async def list_groups(self) -> list[str]:
        return list(self._state.scans.keys())

    async def list_scans(self, group: str) -> list[str]:
        return list(self._state.scans.get(group, {}).keys())

    async def download(self, group: str, scan_name: str) -> bytes:
        scans = self._state.scans.get(group, {})
        if scan_name not in scans:
            raise FileNotFoundError(
                f"Scan '{scan_name}' not found in group '{group}'"
            )
        return scans[scan_name]


class OdysseyStatusSimulated(InstrumentStatusBackend):
    """Simulated status backend for Odyssey."""

    def __init__(self, state: OdysseyState) -> None:
        self._state = state

    async def read_status(self) -> InstrumentStatusReading:
        return InstrumentStatusReading(
            state=self._state.scanner_state,
            current_user=self._state.current_user,
            progress=self._state.progress,
            time_remaining="",
            lid_open=self._state.lid_open,
        )
