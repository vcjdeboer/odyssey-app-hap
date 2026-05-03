"""Image retrieval capability frontend — public API for downloading scan data.

Usage (via device)::

    groups = await odyssey.images.list_groups()
    scans = await odyssey.images.list_scans("public")
    tiff_bytes = await odyssey.images.download("public", "western_01")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plr_v4.capabilities.base import need_setup
from plr_v4.signals import SignalX

if TYPE_CHECKING:
    from plr_v4.capabilities.image_retrieval.backend import ImageRetrievalBackend


class ImageRetrieval:
    """Image retrieval capability frontend.

    Wraps an ImageRetrievalBackend with setup guards and signal emission.
    """

    download_action = SignalX(description="Image download triggered")

    def __init__(self, backend: ImageRetrievalBackend) -> None:
        self.backend = backend
        self._setup_finished = False

    async def setup(self) -> None:
        await self.backend.setup()

    async def stop(self) -> None:
        await self.backend.teardown()

    @need_setup
    async def list_groups(self) -> list[str]:
        return await self.backend.list_groups()

    @need_setup
    async def list_scans(self, group: str) -> list[str]:
        return await self.backend.list_scans(group)

    @need_setup
    async def download(self, group: str, scan_name: str) -> bytes:
        data = await self.backend.download(group, scan_name)
        self.download_action.emit(
            payload={"group": group, "scan": scan_name, "size": len(data)}
        )
        return data
