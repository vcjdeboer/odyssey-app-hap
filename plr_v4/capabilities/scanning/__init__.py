from plr_v4.capabilities.scanning.backend import ScanningBackend
from plr_v4.capabilities.scanning.capability import Scanning


class ScanningError(Exception):
    """Capability-generic exception for scanning failures.

    Vendor backends should raise a subclass that ALSO inherits from the
    vendor's driver-level exception, so callers can ``except`` either
    axis (capability-generic or vendor-specific). See
    ``plr_v4/odyssey/errors.py`` for the Odyssey example.
    """


__all__ = ["ScanningBackend", "Scanning", "ScanningError"]
