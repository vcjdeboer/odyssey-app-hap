"""TIFF identity + provenance tagging for Odyssey scans.

Embeds the device's identity (PIDInst Handle URI, landing page, name),
scan parameters, session context, and any display-time transforms into
TIFF metadata so a scan lifted out of its surrounding files still
resolves back to the instrument it came from AND is reproducible from
the file alone.

Tag layout:

- ``270`` ImageDescription — **preserved verbatim from the vendor**. If
  the vendor didn't set it, our JSON goes here as a fallback.
- ``305`` Software — **preserved verbatim from the vendor**. If unset,
  our software tag goes here as a fallback.
- ``65000`` (private tag) — Our JSON payload with identity + scan_params
  + session + transforms. Reserved-for-private-use range (65000-65535)
  so it never collides with vendor tags.

The functions are no-ops when the card carries no identity AND no
per-call ``scan_name``/``channel`` is supplied. They never raise on a
parse or save failure — the original bytes are returned so a download
is never lost to a tagging failure.

Uses ``tifffile`` under the hood (not ``PIL.Image.save``) so vendor
tags round-trip cleanly. Also unlocks ImageJ HyperStack writing for
the two-channel colored-overlay export path.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from plr_v4.device_card import DeviceCard

logger = logging.getLogger(__name__)

DEFAULT_SOFTWARE_TAG = "PyLabRobot Odyssey"

# Private TIFF tag for our JSON payload. Range 65000-65535 is reserved
# for private use (TIFF 6.0 spec); vendors won't touch it.
PRIVATE_JSON_TAG = 65000


def build_identity_description(
    card: "DeviceCard",
    *,
    scan_name: str = "",
    channel: Optional[int] = None,
    scan_params: Optional[dict[str, Any]] = None,
    session: Optional[dict[str, Any]] = None,
    transforms: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> str:
    """Render the full provenance payload as a compact JSON string.

    Keys:
        identity: DeviceCard identity slot (pid, landing_page, name).
        scan_name: user-typed scan name.
        channel: 700 or 800 for per-channel files; None for composites.
        scan_params: what the scanner was told to do (intensity, focus,
            resolution, quality, origin, extent, channels).
        session: who + when + which experiment (operator, experiment_id,
            scan_completed_at).
        transforms: display-time B/C/invert/crop/tint applied to derive
            this file from raw pixels. ``None`` = raw, no transforms.
        extra: escape hatch for any other keys.
    """
    payload: dict[str, Any] = {}
    if card.identity:
        payload["identity"] = dict(card.identity)
    if scan_name:
        payload["scan_name"] = scan_name
    if channel is not None:
        payload["channel"] = channel
    if scan_params:
        payload["scan_params"] = scan_params
    if session:
        payload["session"] = session
    # Explicit `None` means "raw, no transforms" — record it so tools
    # know the file is quantifiable directly. Empty dict means the same
    # but tests can distinguish "not passed" from "explicitly raw".
    if transforms is not None:
        payload["transforms"] = transforms
    if extra:
        payload.update(extra)
    return json.dumps(payload, separators=(",", ":"))


def tag_tiff_with_identity(
    raw_bytes: bytes,
    card: "DeviceCard",
    *,
    scan_name: str = "",
    channel: Optional[int] = None,
    scan_params: Optional[dict[str, Any]] = None,
    session: Optional[dict[str, Any]] = None,
    transforms: Optional[dict[str, Any]] = None,
    software_tag: str = DEFAULT_SOFTWARE_TAG,
) -> bytes:
    """Re-emit a TIFF with our provenance JSON in private tag 65000.

    Preserves vendor's tags 270 (ImageDescription) and 305 (Software)
    verbatim — our data goes into a private tag so we never clobber
    vendor provenance. If vendor didn't set 270/305, we populate them
    with our own identity JSON / software tag as a helpful fallback.

    Returns ``raw_bytes`` unchanged when the card has no identity and
    no per-call context is supplied (so unconfigured users see no
    behavior change), when tifffile is not importable, or when the TIFF
    fails to parse / re-save.
    """
    if not raw_bytes:
        return raw_bytes
    if not (card.identity or scan_name or channel is not None
            or scan_params or session or transforms is not None):
        return raw_bytes
    try:
        import tifffile
    except ImportError:
        logger.info("TIFF re-tag skipped (tifffile not available)")
        return raw_bytes
    try:
        with tifffile.TiffFile(io.BytesIO(raw_bytes)) as tif:
            page = tif.pages[0]
            pixels = page.asarray()
            vendor_description = ""
            vendor_software = ""
            for tag in page.tags:
                if tag.code == 270:
                    vendor_description = str(tag.value) if tag.value else ""
                elif tag.code == 305:
                    vendor_software = str(tag.value) if tag.value else ""
            photometric = page.photometric
    except Exception as e:
        logger.info("TIFF re-tag skipped (parse failed): %s", e)
        return raw_bytes

    our_json = build_identity_description(
        card,
        scan_name=scan_name,
        channel=channel,
        scan_params=scan_params,
        session=session,
        transforms=transforms,
    )

    # Preserve vendor 270/305 verbatim; fall back to ours only if vendor
    # left them empty. Analysts recognise LI-COR's own strings — never
    # overwrite them.
    description = vendor_description or our_json
    software = vendor_software or software_tag

    # tifffile extratags format: (code, dtype, count, value, writeonce)
    # dtype 's' = ASCII string.
    extratags = [
        (PRIVATE_JSON_TAG, 's', 0, our_json, False),
    ]

    try:
        out = io.BytesIO()
        import tifffile as _tf
        _tf.imwrite(
            out,
            pixels,
            photometric=photometric,
            description=description,
            software=software,
            extratags=extratags,
        )
        return out.getvalue()
    except Exception as e:
        logger.info("TIFF re-tag skipped (save failed): %s", e)
        return raw_bytes


def write_hyperstack(
    ch700,  # numpy.ndarray (H, W) uint16
    ch800,  # numpy.ndarray (H, W) uint16
    card: "DeviceCard",
    *,
    scan_name: str = "",
    scan_params: Optional[dict[str, Any]] = None,
    session: Optional[dict[str, Any]] = None,
    software_tag: str = DEFAULT_SOFTWARE_TAG,
) -> bytes:
    """Write an ImageJ HyperStack TIFF (C=2, 16-bit per channel).

    Fiji opens this already colorized red (700 nm) / green (800 nm)
    thanks to the LUT metadata, but the two underlying channel arrays
    stay separate 16-bit grayscale planes so downstream densitometry
    works directly on the file. Users can switch LUTs (e.g. to Grays
    for pure intensity) via Image → Lookup Tables in Fiji without
    re-exporting.

    Non-Fiji viewers (Windows Photos, Photoshop) show only the first
    channel (700 nm) as 16-bit grayscale — the trilemma of "16-bit +
    red-green-on-Windows + standard-portable, pick 2 of 3".
    """
    import numpy as np
    import tifffile

    if ch700.shape != ch800.shape:
        raise ValueError(
            f"Channel shapes differ: 700={ch700.shape} 800={ch800.shape}"
        )
    stack = np.stack([ch700.astype(np.uint16), ch800.astype(np.uint16)])

    our_json = build_identity_description(
        card,
        scan_name=scan_name,
        scan_params=scan_params,
        session=session,
        transforms=None,  # HyperStack is raw pixels + LUT-only display
    )

    # ImageJ LUT metadata — display-time only, doesn't touch pixel data.
    # Shape (3, 256): rows are R/G/B ramps.
    lut_red = np.zeros((3, 256), dtype=np.uint8)
    lut_red[0] = np.arange(256, dtype=np.uint8)
    lut_green = np.zeros((3, 256), dtype=np.uint8)
    lut_green[1] = np.arange(256, dtype=np.uint8)

    extratags = [
        (PRIVATE_JSON_TAG, 's', 0, our_json, False),
    ]

    out = io.BytesIO()
    tifffile.imwrite(
        out,
        stack,
        photometric='minisblack',
        imagej=True,
        metadata={
            'axes': 'CYX',
            'mode': 'composite',
            'LUTs': [lut_red, lut_green],
        },
        description=our_json,
        software=software_tag,
        extratags=extratags,
    )
    return out.getvalue()
