"""Odyssey image retrieval backend — concrete ImageRetrievalBackend for HTTP.

Downloads scan images from the instrument's internal hard disk via
the /scan/image endpoint with XML query parameters.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from plr_v4.capabilities.image_retrieval import ImageRetrievalBackend

if TYPE_CHECKING:
    from plr_v4.odyssey.connection import OdysseyDriver

logger = logging.getLogger(__name__)


class OdysseyImageBackend(ImageRetrievalBackend):
    """Concrete image retrieval backend for LI-COR Odyssey Classic."""

    def __init__(self, driver: OdysseyDriver) -> None:
        self._driver = driver

    async def list_groups(self) -> list[str]:
        html = await self._driver.list_scan_groups()
        return self._driver.parse_select_options(html, "avail")

    async def list_scans(self, group: str) -> list[str]:
        html = await self._driver.list_scan_groups()
        return self._driver.parse_select_options(html, "preset")

    async def download(self, group: str, scan_name: str) -> bytes:
        ch700 = await self._driver.download_tiff(group, scan_name, 700)
        ch800 = await self._driver.download_tiff(group, scan_name, 800)
        return ch700 + ch800

    async def download_channel(
        self, group: str, scan_name: str, channel: int
    ) -> bytes:
        return await self._driver.download_tiff(group, scan_name, channel)

    async def get_preview(
        self,
        group: str,
        scan_name: str,
        contrast_700: int = 5,
        contrast_800: int = 5,
        channels: str = "700 800",
        background: str = "black",
    ) -> bytes:
        return await self._driver.get_jpeg_preview(
            group, scan_name,
            contrast_700=contrast_700,
            contrast_800=contrast_800,
            channels=channels,
            background=background,
        )

    async def download_scan_log(self, group: str, scan_name: str) -> str:
        return await self._driver.download_scan_log(group, scan_name)
