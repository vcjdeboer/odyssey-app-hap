"""Scanning backend ABC — abstract interface for scan control.

Concrete backends implement these methods to configure and control
fluorescence scans on specific instruments.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class ScanningBackend(ABC):
    """Backend ABC for scanning capability.

    Concrete backends implement:
        configure(params)
        start()
        stop()
        pause()
        cancel()
    """

    @abstractmethod
    async def configure(self, params: Any) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def pause(self) -> None: ...

    @abstractmethod
    async def cancel(self) -> None: ...

    async def setup(self) -> None:
        """Override in concrete backend if needed."""

    async def teardown(self) -> None:
        """Safety shutdown: cancel any active scan."""
        try:
            await self.cancel()
        except Exception:
            logger.exception("Failed to cancel scan during teardown")
