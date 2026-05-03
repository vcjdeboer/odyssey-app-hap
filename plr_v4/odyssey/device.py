"""Device class for LI-COR Odyssey Classic infrared imaging system.

The three-line notebook usage:

    import asyncio
    from plr_v4.odyssey import OdysseyClassic, ScanParameters

    async def main():
        # Simulated — no hardware required.
        async with OdysseyClassic.simulated() as odyssey:
            await odyssey.scanning.configure(ScanParameters(name="demo"))
            await odyssey.scanning.start()
            status = await odyssey.wait_until_done()
            print(status.state)  # "Completed"
            tiff = await odyssey.images.download("odyssey", "demo")
            print(f"{len(tiff)} bytes")

    asyncio.run(main())

For real hardware, use `.from_env()` — it reads ODYSSEY_USER / ODYSSEY_PASS
from the environment (FR-027) so credentials never live in code:

    async with OdysseyClassic.from_env(host="169.254.206.190") as odyssey:
        status = await odyssey.scan(ScanParameters(name="western_01"))
        tiff = await odyssey.images.download("odyssey", "western_01")
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional, TYPE_CHECKING

from plr_v4.device import Device
from plr_v4.capabilities.scanning import Scanning
from plr_v4.capabilities.image_retrieval import ImageRetrieval
from plr_v4.capabilities.instrument_status import (
    InstrumentStatus, InstrumentStatusReading,
)
from plr_v4.odyssey.scan_backend import OdysseyScanBackend
from plr_v4.odyssey.image_backend import OdysseyImageBackend
from plr_v4.odyssey.status_backend import OdysseyStatusBackend, normalize_state
from plr_v4.odyssey.device_card import ODYSSEY_CLASSIC_BASE

if TYPE_CHECKING:
    from plr_v4.odyssey.connection import OdysseyDriver, ScanParameters
    from plr_v4.device_card import DeviceCard

logger = logging.getLogger(__name__)

# States that end a scan session.
#
# IMPORTANT: 'Idle' IS a terminal state because the real Odyssey
# instrument transitions back to 'Idle' when a scan finishes — it
# never reports 'Completed' (that's a simulator convention added in
# Phase 24). Excluding 'Idle' here breaks scan completion detection
# on real hardware. The fresh-terminal-state guard (Pattern 1) is
# what makes 'Idle' safe to include — we only accept it as completion
# AFTER seeing a non-terminal observation (i.e., the instrument
# transitioned through Scanning).
_TERMINAL_STATES = frozenset({"Idle", "Completed", "Stopped", "Failed"})


class OdysseyClassic(Device):
    """LI-COR Odyssey Classic (model 9120) infrared imaging system.

    Capabilities:
        scanning: configure and control fluorescence scans.
        images:   download scan TIFF files from instrument storage.
        status:   poll instrument state.

    Construct directly if you already own an OdysseyDriver. Otherwise
    prefer the two factories:

    - ``OdysseyClassic.from_env(host=...)`` for real hardware — reads
      credentials from ODYSSEY_USER / ODYSSEY_PASS.
    - ``OdysseyClassic.simulated()`` for an in-memory backend that runs
      anywhere (CI, notebooks, dev machines).
    """

    def __init__(
        self,
        driver: Optional["OdysseyDriver"] = None,
        card: Optional["DeviceCard"] = None,
        *,
        _simulated_state: object = None,
    ) -> None:
        """Build an OdysseyClassic device.

        Pass ``driver`` for real hardware. ``_simulated_state`` is an
        internal parameter used by :meth:`simulated` — notebook users
        should call the factory instead.
        """
        super().__init__(card=card or ODYSSEY_CLASSIC_BASE)
        self._driver = driver
        self._sim_state = _simulated_state

        if _simulated_state is not None:
            from plr_v4.odyssey.simulated import (
                OdysseyScanSimulated,
                OdysseyImageSimulated,
                OdysseyStatusSimulated,
            )
            self.scanning = Scanning(backend=OdysseyScanSimulated(_simulated_state))
            self.images = ImageRetrieval(backend=OdysseyImageSimulated(_simulated_state))
            self.status = InstrumentStatus(
                backend=OdysseyStatusSimulated(_simulated_state)
            )
        else:
            if driver is None:
                raise ValueError(
                    "OdysseyClassic needs either a driver (real hardware) "
                    "or _simulated_state (via .simulated()). Use "
                    "OdysseyClassic.from_env(host=...) or OdysseyClassic.simulated()."
                )
            self.scanning = Scanning(backend=OdysseyScanBackend(driver))
            self.images = ImageRetrieval(backend=OdysseyImageBackend(driver))
            self.status = InstrumentStatus(
                backend=OdysseyStatusBackend(driver)
            )

        self._drivers.extend([self.scanning, self.images, self.status])

    # -- Factories -----------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        host: Optional[str] = None,
        port: int = 80,
        timeout: float = 60.0,
        card: Optional["DeviceCard"] = None,
    ) -> "OdysseyClassic":
        """Build a real-hardware OdysseyClassic from environment variables.

        Reads ODYSSEY_USER / ODYSSEY_PASS (FR-027). If ``host`` is None,
        ODYSSEY_HOST is read too. Never embeds credentials in code. Pass
        ``card=`` to override the model-base card (e.g. with a merged
        instance card carrying this unit's PID identity).
        """
        from plr_v4.odyssey.connection import OdysseyDriver
        drv = OdysseyDriver.from_env(host=host, port=port, timeout=timeout)
        return cls(driver=drv, card=card)

    @classmethod
    def simulated(cls, card: Optional["DeviceCard"] = None) -> "OdysseyClassic":
        """Build an in-memory OdysseyClassic — no hardware required.

        Useful for notebooks, CI, and dev machines. Every capability is
        backed by the Odyssey*Simulated classes so behaviour matches the
        real driver (FR-029 simulator parity). Pass ``card=`` to inject
        a merged instance card (e.g. for PID-aware exports in tests).
        """
        from plr_v4.odyssey.simulated import OdysseyState
        return cls(_simulated_state=OdysseyState(), card=card)

    # -- Lifecycle -----------------------------------------------------------

    async def setup(self) -> None:
        """Open the driver (if any), then set up capabilities."""
        if self._driver is not None:
            await self._driver.setup()
        await super().setup()

    async def stop(self) -> None:
        """Stop capabilities, then stop the driver (if any)."""
        await super().stop()
        if self._driver is not None:
            await self._driver.stop()

    # -- Convenience helpers -------------------------------------------------

    async def wait_until_done(
        self,
        poll_interval: float = 1.0,
        timeout: Optional[float] = None,
        on_progress: Optional[Callable[[InstrumentStatusReading], None]] = None,
        require_fresh: bool = True,
    ) -> InstrumentStatusReading:
        """Poll status until the instrument reaches a terminal state.

        With ``require_fresh=True`` (default) implements the
        stale-terminal-state guard from
        ``docs/odyssey-http-patterns.md`` Pattern 1: if the *first*
        observed state is already terminal (the instrument is done
        from a previous scan), require a non-terminal observation
        before accepting the next terminal as 'this scan finished'.
        Without that guard, a caller racing wait_until_done against a
        just-completed scan would silently receive that previous run's
        status.

        Set ``require_fresh=False`` when the caller knows the wait was
        triggered by an action that just initiated a new run — the
        :meth:`scan` helper does this, since it owns the configure+start
        sequence and the protection isn't needed.

        Returns the final :class:`InstrumentStatusReading`. Raises
        :class:`asyncio.TimeoutError` if ``timeout`` elapses first.
        """
        loop = asyncio.get_event_loop()
        deadline = None if timeout is None else loop.time() + timeout

        # Capture the initial state. If require_fresh is on AND the
        # instrument is already in a terminal state, demand a transition
        # out before accepting the next terminal observation.
        initial = await self.status.read()
        if on_progress is not None:
            on_progress(initial)
        require_state_change = (
            require_fresh
            and normalize_state(initial.state) in _TERMINAL_STATES
        )

        while True:
            status = await self.status.read()
            if on_progress is not None:
                on_progress(status)
            state = normalize_state(status.state)
            if state not in _TERMINAL_STATES:
                # Saw a non-terminal — any subsequent terminal is fresh.
                require_state_change = False
            elif not require_state_change:
                return status
            if deadline is not None and loop.time() > deadline:
                raise asyncio.TimeoutError(
                    f"Scan did not reach a {'fresh ' if require_fresh else ''}"
                    f"terminal state within {timeout:.0f}s "
                    f"(last state={status.state!r})"
                )
            await asyncio.sleep(poll_interval)

    async def scan(
        self,
        params: "ScanParameters",
        poll_interval: float = 1.0,
        on_progress: Optional[Callable[[InstrumentStatusReading], None]] = None,
    ) -> InstrumentStatusReading:
        """Configure → Start → wait for completion. Returns the final status.

        One-shot helper for the common notebook flow. Does not download the
        result — call ``odyssey.images.download(group, name)`` afterwards.

        Example::

            status = await odyssey.scan(ScanParameters(name="demo"))
            # Real hardware reports 'Idle' when a scan finishes; the
            # simulator reports 'Completed'. Both are terminal — check
            # for either, or just for the absence of an error state.
            if status.state in ("Idle", "Completed"):
                tiff = await odyssey.images.download("odyssey", "demo")
        """
        await self.scanning.configure(params)
        await self.scanning.start()
        # scan() owns the configure+start sequence — no risk of latching
        # onto a previous run's terminal state, so opt out of the
        # fresh-terminal guard. Standalone wait_until_done() callers
        # still get protection by default.
        return await self.wait_until_done(
            poll_interval=poll_interval,
            on_progress=on_progress,
            require_fresh=False,
        )
