"""Odyssey scan backend — concrete ScanningBackend for HTTP/CGI control.

Delegates to OdysseyDriver which POSTs to the Perl CGI endpoints.

Workflow: config.pl -> initializing.pl (7s, FR-025) -> command.pl?action=start
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from plr_v4.capabilities.scanning import ScanningBackend
from plr_v4.odyssey.errors import OdysseyScanError

if TYPE_CHECKING:
    from plr_v4.odyssey.connection import OdysseyDriver, ScanParameters

logger = logging.getLogger(__name__)


@dataclass
class StopResult:
    """Outcome of a graceful Stop (FR-021).

    Mirrors the JSON shape returned by POST /api/scan/stop (see
    specs/022-odyssey-app-lab-fixes/contracts/odyssey-app-http-api.md).
    """

    state: str  # Expected: "Stopped"
    partial: bool
    channels_available: list[int]


# Max seconds to wait for the instrument to settle at Idle after Stop.
_STOP_IDLE_TIMEOUT_SEC = 15.0
_STOP_IDLE_POLL_SEC = 1.0


class OdysseyScanBackend(ScanningBackend):
    """Concrete scanning backend for LI-COR Odyssey Classic.

    Sends scan parameters via HTTP POST to config.pl, waits for the
    7-second initialization countdown, then controls the scan via
    command.pl?action=start|stop|pause|cancel.
    """

    def __init__(self, driver: OdysseyDriver) -> None:
        self._driver = driver
        self._current_scan: str = ""
        self._current_group: str = ""

    async def configure(self, params: ScanParameters) -> None:
        status = await self._driver.get_status()
        if status["state"].lower() == "paused":
            logger.warning(
                "Scanner is paused from previous session — stopping first"
            )
            await self._driver.stop_from_status()

        await self._driver.configure_scan(params)
        self._current_scan = params.name
        self._current_group = params.group

        await self._driver.wait_initialization(
            params.name, params.group
        )

    async def start(self) -> None:
        await self._driver.start_scan()

    async def stop(self) -> None:
        await self._driver.stop_scan()

    async def pause(self) -> None:
        await self._driver.pause_scan()

    async def cancel(self) -> None:
        await self._driver.cancel_scan()

    async def stop_and_save(self) -> StopResult:
        """Graceful Stop that saves whatever has been scanned (FR-021).

        1. Issue command.pl?action=stop.
        2. Poll status until the instrument reports Idle (bounded by
           _STOP_IDLE_TIMEOUT_SEC) — this is what makes the Stop
           "auto-return to idle" semantics real.
        3. Probe which channel TIFFs were written (the instrument writes
           whatever it managed to acquire before we interrupted it).

        Raises RuntimeError if the instrument does not settle at Idle
        within the timeout — in that case the caller's retry path should
        kick in rather than silently reporting success.
        """
        if not self._current_scan or not self._current_group:
            # Stop called before anything was configured — nothing to save.
            await self._driver.stop_scan()
            return StopResult(state="Stopped", partial=False,
                              channels_available=[])

        await self._driver.stop_scan()

        deadline = asyncio.get_event_loop().time() + _STOP_IDLE_TIMEOUT_SEC
        while True:
            status = await self._driver.get_status()
            state = (status.get("state") or "").strip().lower()
            if state in ("idle", "stopped"):
                break
            if asyncio.get_event_loop().time() > deadline:
                raise OdysseyScanError(
                    f"Instrument did not return to Idle within "
                    f"{_STOP_IDLE_TIMEOUT_SEC:.0f}s after Stop "
                    f"(last state={status.get('state')!r})"
                )
            await asyncio.sleep(_STOP_IDLE_POLL_SEC)

        # Probe channel availability by attempting a small TIFF download
        # for each. The instrument writes partial TIFFs at whatever row
        # the scan had reached; a failed download means that channel did
        # not produce any data (e.g., channel_800 disabled, or stop was
        # too early).
        channels_available: list[int] = []
        for ch in (700, 800):
            try:
                data = await self._driver.download_tiff(
                    self._current_group, self._current_scan, ch
                )
                if data:
                    channels_available.append(ch)
            except Exception as e:
                logger.info("Channel %d not available after Stop: %s", ch, e)

        return StopResult(
            state="Stopped",
            partial=bool(channels_available),
            channels_available=channels_available,
        )

    async def estimate_time(self, params: ScanParameters) -> str:
        return await self._driver.estimate_scan_time(params)

    async def get_progress(self) -> dict[str, str]:
        if self._current_scan:
            return await self._driver.get_scan_progress(
                self._current_scan, self._current_group
            )
        return {"dimensions": "", "file_size": "", "time_left": ""}
