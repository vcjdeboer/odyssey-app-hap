"""Odyssey status backend — concrete InstrumentStatusBackend for HTTP.

Fetches and parses the status page at /scanapp/util/status/ to extract
scanner state, progress, and lid status.

State is normalized to the InstrumentState literal so downstream consumers
(WebSocket clients, button-state logic) can rely on exact string equality.
See specs/022-odyssey-app-lab-fixes/data-model.md §1.
"""

from __future__ import annotations

import logging
from typing import Literal, TYPE_CHECKING

from plr_v4.capabilities.instrument_status import (
    InstrumentStatusBackend,
    InstrumentStatusReading,
)

if TYPE_CHECKING:
    from plr_v4.odyssey.connection import OdysseyDriver

logger = logging.getLogger(__name__)


# Canonical state set — see data-model.md §1.
InstrumentState = Literal[
    "Idle", "Configured", "Initializing", "Scanning",
    "Paused", "Stopped", "Completed", "Failed",
]

# Mapping from instrument strings (lowercase) to canonical state.
_STATE_MAP: dict[str, InstrumentState] = {
    "idle": "Idle",
    "configured": "Configured",
    "initializing": "Initializing",
    "scanning": "Scanning",
    "escanning": "Scanning",  # Odyssey firmware quirk
    "paused": "Paused",
    "stopped": "Stopped",
    "completed": "Completed",
    "failed": "Failed",
    "error": "Failed",
}


def normalize_state(raw: str) -> InstrumentState:
    """Map a raw instrument state string to the canonical InstrumentState.

    Unrecognized values (including the parser's ``"Unknown"`` sentinel
    when the status HTML doesn't match the regex) fall back to ``"Idle"``
    rather than ``"Failed"``. ``"Failed"`` was the original choice to
    avoid getting stuck in a phantom non-terminal state, but it has a
    sharper failure mode: the UI screams ``"Failed"`` whenever the
    parser misses, blocking Configure / Start even when the instrument
    is fine. ``"Idle"`` keeps the UI usable; the stale-terminal-state
    guard in :meth:`OdysseyClassic.wait_until_done` (Phase 31, Pattern
    1) already protects against latching onto a phantom terminal.
    """
    key = (raw or "").strip().lower()
    if key in _STATE_MAP:
        return _STATE_MAP[key]
    logger.warning(
        "Unknown instrument state %r — mapping to 'Idle' (parser miss?)", raw
    )
    return "Idle"


class OdysseyStatusBackend(InstrumentStatusBackend):
    """Concrete status backend for LI-COR Odyssey Classic.

    Polls /scanapp/util/status/ and parses the HTML, normalising the
    scraped state onto the canonical InstrumentState literal.
    """

    def __init__(self, driver: OdysseyDriver) -> None:
        self._driver = driver

    async def read_status(self) -> InstrumentStatusReading:
        raw = await self._driver.get_status()

        state = normalize_state(raw["state"])

        try:
            progress = float(raw["progress"])
        except (ValueError, TypeError):
            progress = 0.0

        lid_status = raw.get("lid_status", "closed")

        return InstrumentStatusReading(
            state=state,
            current_user=raw.get("current_user", ""),
            progress=progress,
            time_remaining=raw.get("time_remaining", ""),
            lid_open=lid_status.lower() != "closed",
        )

    async def force_stop(self) -> None:
        """Stop the scanner from the status page."""
        logger.warning("Force-stopping scanner from status page")
        await self._driver.stop_from_status()
