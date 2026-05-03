"""Image retrieval backend ABC — abstract interface for downloading scan data.

Concrete backends implement these methods to list and download
scan images from specific instruments.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ImageRetrievalBackend(ABC):
    """Backend ABC for image retrieval capability.

    Concrete backends implement:
        list_groups()
        list_scans(group)
        download(group, scan_name)
    """

    @abstractmethod
    async def list_groups(self) -> list[str]: ...

    @abstractmethod
    async def list_scans(self, group: str) -> list[str]: ...

    @abstractmethod
    async def download(self, group: str, scan_name: str) -> bytes: ...

    async def setup(self) -> None:
        """Override in concrete backend if needed."""

    async def teardown(self) -> None:
        """No cleanup needed by default."""
