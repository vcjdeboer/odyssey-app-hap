"""Odyssey Classic HTTP connection — CGI endpoint client.

The LI-COR Odyssey Classic (model 9120) runs an embedded Linux server
(Apache/1.3.27 + mod_perl/1.23 on Red Hat Linux) that serves Perl CGI
scripts over HTTP. This connection layer wraps an aiohttp session with
Basic Auth and provides typed methods for each endpoint.

API reverse-engineered from HAR captures of the browser interface.

Endpoints:
    Scan setup:
        POST /scanapp/scan/nonjava/configure.pl     — configure scan parameters
        GET  /scanapp/scan/nonjava/command.pl        — ?action=start|stop|pause|cancel
        GET  /scanapp/scan/nonjava/console.pl        — scan console page
        GET  /scanapp/scan/nonjava/initializing.pl   — ?scan=<name>&scangroup=<group>&timeout=<n>
        GET  /scanapp/scan/nonjava/time.pl           — scan time estimate

    Imaging:
        GET  /scanapp/imaging/nonjava/info.pl        — scan progress (Time Left, Progress %)
        POST /scanapp/imaging/nonjava/openimage.pl   — render JPEG preview
        GET  /scan/image?xml=<xml>                   — fetch JPEG preview
        GET  /scan/image/<name>-<ch>.tif?xml=<xml>   — download raw TIFF
        POST /scanapp/imaging/nonjava/save.pl        — save panel with TIFF links
        GET  /scanapp/imaging/nonjava/savelog.pl      — download scan log

    Status:
        GET  /scanapp/util/status/                   — instrument status page
        POST /scanapp/util/status/status             — stop scan from status page

    Admin:
        GET  /scanapp/admin/                         — utilities page
        POST /scanapp/admin/admin/index              — ?action=InitiateShutdown

Auth: HTTP Basic Auth, realm "LICOR-Odyssey".
Transport: TCP/IP, 10/100Base-T Ethernet.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

from pylabrobot.capabilities.capability import BackendParams

from plr_v4.odyssey.errors import OdysseyError, OdysseyImageError, OdysseyScanError

logger = logging.getLogger(__name__)


# Transport errors that are worth retrying — connection-level transients.
# Server-level failures (4xx/5xx, which surface as ClientResponseError if
# raise_for_status() is called, or as a non-200 status on the response
# object otherwise) are NOT retried — see Pattern 4 in
# docs/odyssey-http-patterns.md.
_RETRYABLE_EXCEPTIONS = (
    aiohttp.ClientConnectionError,
    asyncio.TimeoutError,
    ConnectionResetError,
    OSError,
)
_HTTP_RETRY_ATTEMPTS = 3
_HTTP_RETRY_DELAY = 0.25

_SCAN_BASE = "/scanapp/scan/nonjava"
_IMAGE_BASE = "/scanapp/imaging/nonjava"
_STATUS_BASE = "/scanapp/util/status"

# Hardware protocol invariants (see specs/022-odyssey-app-lab-fixes/contracts/
# odyssey-driver-protocol.md). These are contracts with the Odyssey Classic
# firmware — do NOT change without updating the per-FR tests.
_CONFIGURE_URL_PATH = f"{_SCAN_BASE}/configure.pl"  # FR-022: configure.pl (firmware 2.1.12)
_CHANNEL_SENTINEL = "x"  # FR-023: hidden 'channel' field must be literal "x"
_INIT_POLL_STEPS = 7  # FR-025: full 7→1 countdown
DEFAULT_GROUP = "odyssey"  # FR-026: "odyssey" is the only operational group
_CRED_ENV_USER = "ODYSSEY_USER"  # FR-027
_CRED_ENV_PASS = "ODYSSEY_PASS"  # FR-027


@dataclass
class ScanParameters(BackendParams):
    """Scan configuration parameters for the Odyssey Classic.

    Field names match the configure.pl form exactly, as captured from
    HAR files of the browser interface.

    Attributes:
        name: Scan name (no ;/?:@=&<>"#%{}|^~[] characters).
        group: Scan group name (e.g., "public", "odyssey").
        resolution: Scan resolution in µm (21, 42, 84, 169, 337, or "preview").
        quality: Scan quality ("lowest", "low", "medium", "high", "highest").
        intensity_700: 700 nm channel intensity. Values: L2, L1.5, L1, L0.5,
            0.5, 1, 1.5, 2, ..., 10 (in 0.5 steps).
        intensity_800: 800 nm channel intensity (same range).
        channel_700: Enable 700 nm channel.
        channel_800: Enable 800 nm channel.
        origin_x: Scan origin X in cm (0-25).
        origin_y: Scan origin Y in cm (0-25).
        width: Scan width in cm (origin_x + width <= 25).
        height: Scan height in cm (origin_y + height <= 25).
        focus: Focus offset in mm (0.0-4.0). 0 for membranes, ~1.0 for gels,
            3.0 for microplates.
        comment: Free text comment.
        preset: Preset name to load (empty for manual config).
    """

    name: str = "scan"
    group: str = DEFAULT_GROUP  # FR-026
    resolution: str = "169"
    quality: str = "medium"
    intensity_700: str = "5"
    intensity_800: str = "5"
    channel_700: bool = True
    channel_800: bool = True
    origin_x: int = 0
    origin_y: int = 0
    width: int = 10
    height: int = 10
    focus: float = 0.0
    comment: str = ""
    preset: str = ""

    def to_form_data(self) -> dict[str, str]:
        """Convert to form data dict for POST to configure.pl.

        Field names match the HTML form exactly:
            channel, scan, scangroup, avail, preset, resolution, quality,
            intensity700, intensity800, chan700, chan800, x0, y0, width,
            height, x1, y1, focus, comment, prename
        """
        data: dict[str, str] = {
            "channel": _CHANNEL_SENTINEL,  # FR-023: must be "x" (form has typo VAUE=x)
            "scan": self.name,
            "scangroup": self.group,
            "avail": self.group,
            "preset": self.preset,
            "resolution": str(self.resolution),
            "quality": self.quality,
            "intensity700": str(self.intensity_700),
            "intensity800": str(self.intensity_800),
            "x0": str(self.origin_x),
            "y0": str(self.origin_y),
            "width": str(self.width),
            "height": str(self.height),
            "x1": str(self.origin_x + self.width),  # computed
            "y1": str(self.origin_y + self.height),  # computed
            "focus": str(self.focus),
            "comment": self.comment,
            "prename": "",  # only used when saving presets
        }
        # Checkboxes: only include if enabled (absent = unchecked)
        if self.channel_700:
            data["chan700"] = "chan700"
        if self.channel_800:
            data["chan800"] = "chan800"
        return data

    def to_time_params(self) -> dict[str, str]:
        """Convert to query params for time.pl estimation."""
        return {
            "resolution": str(self.resolution),
            "quality": self.quality,
            "x0": str(self.origin_x),
            "y0": str(self.origin_y),
            "x1": str(self.origin_x + self.width),
            "y1": str(self.origin_y + self.height),
        }


def _tiff_xml(group: str, scan_name: str, channel: int) -> str:
    """Build the XML query string for TIFF download.

    The Odyssey serves TIFFs at /scan/image/<name>-<channel>.tif
    with an XML query parameter specifying the scan details.
    """
    return (
        f"<image><in>"
        f"<scangroup>{group}</scangroup>"
        f"<scan>{scan_name}</scan>"
        f"<format>tiff</format>"
        f"<channel>{channel}</channel>"
        f"<clip><x0>0</x0><x1>0</x1><y0>0</y0><y1>0</y1></clip>"
        f"</in></image>"
    )


def _jpeg_xml(
    group: str,
    scan_name: str,
    contrast_700: int = 5,
    contrast_800: int = 5,
    channels: str = "700 800",
    background: str = "black",
    clip: tuple[int, int, int, int] = (0, 0, 0, 0),
    vflip: bool = True,
    hflip: bool = True,
    zoom: int = 1,
) -> str:
    """Build the XML query string for JPEG preview."""
    x0, x1, y0, y1 = clip
    return (
        f"<image><in>"
        f"<scangroup>{group}</scangroup>"
        f"<scan>{scan_name}</scan>"
        f"<zoom>{zoom}</zoom>"
        f"<contrast700>{contrast_700}</contrast700>"
        f"<contrast800>{contrast_800}</contrast800>"
        f"<channel>{channels}</channel>"
        f"<background>{background}</background>"
        f"<clip><x0>{x0}</x0><x1>{x1}</x1><y0>{y0}</y0><y1>{y1}</y1></clip>"
        f"<vflip>{'true' if vflip else 'false'}</vflip>"
        f"<hflip>{'true' if hflip else 'false'}</hflip>"
        f"</in></image>"
    )


class OdysseyDriver:
    """Driver (transport) for the LI-COR Odyssey Classic infrared imager.

    Wraps an aiohttp.ClientSession with Basic Auth and provides async
    methods for each CGI endpoint on the instrument's web server. The
    capability backends share a single OdysseyDriver instance.

    Server: Apache/1.3.27 (Unix) (Red-Hat/Linux) mod_perl/1.23
    Auth realm: LICOR-Odyssey
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 80,
        timeout: float = 60.0,
        group: str = DEFAULT_GROUP,
    ) -> None:
        if not username or not password:
            raise ValueError(
                "OdysseyDriver requires both username and password. "
                f"Use OdysseyDriver.from_env() to read them from "
                f"{_CRED_ENV_USER}/{_CRED_ENV_PASS}."
            )
        self._base_url = f"http://{host}:{port}"
        self._auth = aiohttp.BasicAuth(username, password)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        self._group = group
        # Last status response, cached for the diagnostics endpoint.
        self._last_status_html: str = ""
        self._last_status_http: int = 0
        # Last configure_scan response, cached for diagnostics.
        self._last_configure_url: str = ""
        self._last_configure_http: int = 0
        self._last_configure_body: str = ""
        # FR-027: never log the password; username only.
        logger.info(
            "OdysseyDriver initialised: host=%s group=%s %s=%s",
            host, group, _CRED_ENV_USER, username,
        )

    @classmethod
    def from_env(
        cls,
        host: Optional[str] = None,
        port: int = 80,
        timeout: float = 60.0,
        group: str = DEFAULT_GROUP,
    ) -> "OdysseyDriver":
        """Construct from environment variables (FR-027).

        Reads ODYSSEY_USER and ODYSSEY_PASS from the environment. Raises
        ValueError if either is missing — the app must not silently fall
        back to default credentials against the real instrument.

        If ``host`` is None, ODYSSEY_HOST is read from the environment.
        """
        username = os.environ.get(_CRED_ENV_USER, "")
        password = os.environ.get(_CRED_ENV_PASS, "")
        if not username or not password:
            missing = [
                name for name, val in (
                    (_CRED_ENV_USER, username),
                    (_CRED_ENV_PASS, password),
                ) if not val
            ]
            raise ValueError(
                f"Missing required environment variable(s): "
                f"{', '.join(missing)}. Set them before connecting to the "
                f"real instrument, or unset ODYSSEY_HOST to use simulated mode."
            )
        if host is None:
            host = os.environ.get("ODYSSEY_HOST", "")
            if not host:
                raise ValueError(
                    "No host provided and ODYSSEY_HOST is unset."
                )
        return cls(
            host=host, username=username, password=password,
            port=port, timeout=timeout, group=group,
        )

    @property
    def group(self) -> str:
        return self._group

    @property
    def base_url(self) -> str:
        return self._base_url

    async def setup(self) -> None:
        """Open the HTTP session and verify connectivity."""
        # Force ``Connection: close`` on every request. The Odyssey's
        # embedded Apache 1.3.27 doesn't always handle keep-alive cleanly
        # when a second request arrives before the first response has
        # been fully consumed — exactly the pattern produced by the live
        # preview poll racing a user-driven Configure click. Closing the
        # connection per request is a couple of ms slower but eliminates
        # the inter-request races we see in the field.
        self._session = aiohttp.ClientSession(
            auth=self._auth,
            timeout=self._timeout,
            headers={"Connection": "close"},
        )
        # Verify connectivity — the home page is unprotected
        async with self._session.get(self._base_url) as resp:
            if resp.status != 200:
                raise ConnectionError(
                    f"Cannot reach Odyssey at {self._base_url} "
                    f"(HTTP {resp.status})"
                )
        # Verify auth — the scan page requires login
        url = f"{self._base_url}{_SCAN_BASE}/"
        async with self._session.get(url) as resp:
            if resp.status == 401:
                raise ConnectionError(
                    "Authentication failed — check username/password "
                    "(realm: LICOR-Odyssey)"
                )
        logger.info("Connected to Odyssey at %s", self._base_url)

    async def stop(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _check_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("OdysseyDriver not set up")
        return self._session

    # -- Scan control --------------------------------------------------------

    async def configure_scan(self, params: ScanParameters) -> str:
        """POST scan parameters to configure.pl.

        On success: returns 302 redirect to initializing.pl (7s countdown).
        On error (scanner busy): returns 200 with HTML error page.

        The initializing.pl countdown takes ~7 seconds as the instrument
        configures the DSP, laser voltages, and motor positions.
        """
        session = self._check_session()
        url = f"{self._base_url}{_CONFIGURE_URL_PATH}"  # FR-022
        form_data = params.to_form_data()
        logger.info(
            "Configuring scan: name=%s group=%s res=%s quality=%s",
            params.name, params.group, params.resolution, params.quality,
        )

        logger.info("Form data: %s", form_data)

        last_exc: Optional[Exception] = None
        for attempt in range(_HTTP_RETRY_ATTEMPTS):
            try:
                async with session.post(
                    url, data=form_data, allow_redirects=False
                ) as resp:
                    body = await resp.text()
                    self._last_configure_url = url
                    self._last_configure_http = resp.status
                    self._last_configure_body = body
                    logger.info(
                        "POST %s → HTTP %d, body length %d",
                        url, resp.status, len(body),
                    )
                    if resp.status == 302:
                        redirect = resp.headers.get("Location", "")
                        logger.info("configure.pl redirect → %s", redirect)
                        # Follow the redirect — this triggers hardware initialization
                        if redirect:
                            redirect_url = (
                                redirect if redirect.startswith("http")
                                else f"{self._base_url}{redirect}"
                            )
                            async with session.get(
                                redirect_url, allow_redirects=False,
                            ) as init_resp:
                                logger.info(
                                    "Followed redirect → HTTP %d",
                                    init_resp.status,
                                )
                        return body
                    # 4xx/5xx — server said "no". Don't pretend it
                    # succeeded; the hardware never received the
                    # configuration. Open /api/diagnostics/configure for
                    # the URL, status, and body that the instrument
                    # returned so the cause is visible without
                    # terminal copy-paste.
                    if resp.status >= 400:
                        raise OdysseyScanError(
                            f"Scanner rejected configuration: "
                            f"POST {url} → HTTP {resp.status}. "
                            f"See /api/diagnostics/configure for full body."
                        )
                    # 2xx with an explicit error page in the body. The
                    # firmware embeds a structured ``<Error shorterror="X">
                    # message</Error>`` block — pull the short label and
                    # message out so the UI shows "Scan already exists:
                    # asd already exists." instead of 500 chars of HTML.
                    if "busy" in body.lower() or "<TITLE>Error</TITLE>" in body:
                        m = re.search(
                            r'<Error\s+shorterror="([^"]+)"\s*>\s*(.*?)\s*</Error>',
                            body, re.IGNORECASE | re.DOTALL,
                        )
                        if m:
                            short = m.group(1).strip()
                            detail = re.sub(r'\s+', ' ', m.group(2)).strip()
                            raise OdysseyScanError(
                                f"Scanner rejected configuration — {short}: {detail}"
                            )
                        raise OdysseyScanError(
                            f"Scanner rejected configuration: {body[:500]}"
                        )
                    return body
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                if attempt < _HTTP_RETRY_ATTEMPTS - 1:
                    logger.warning(
                        "configure_scan attempt %d/%d failed (%s) — retrying",
                        attempt + 1, _HTTP_RETRY_ATTEMPTS, exc,
                    )
                    await asyncio.sleep(_HTTP_RETRY_DELAY)
                    continue
        raise OdysseyScanError(
            f"configure_scan failed after {_HTTP_RETRY_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc

    async def wait_initialization(
        self, scan_name: str, group: str, timeout_steps: int = _INIT_POLL_STEPS
    ) -> None:
        """Wait for the instrument to finish initializing.

        FR-025: We MUST poll initializing.pl through the full countdown
        (timeout=7 down to timeout=1) before calling command.pl?action=start.
        Skipping the poll skips hardware prep (motors, laser warm-up) and
        produces invalid scans.
        """
        session = self._check_session()
        logger.info("Waiting %d seconds for hardware initialization...",
                    timeout_steps)
        for t in range(timeout_steps, 0, -1):
            url = f"{self._base_url}{_SCAN_BASE}/initializing.pl"
            params = {"scan": scan_name, "scangroup": group, "timeout": str(t)}
            logger.info("initializing.pl?timeout=%d", t)
            async with session.get(url, params=params, allow_redirects=False) as resp:
                if resp.status == 302 and t <= 1:
                    redirect = resp.headers.get("Location", "")
                    logger.info("Initialization complete → %s", redirect)
                    if redirect:
                        redirect_url = redirect if redirect.startswith("http") \
                            else f"{self._base_url}{redirect}"
                        async with session.get(
                            redirect_url, allow_redirects=True
                        ) as _:
                            pass
                    return
            await asyncio.sleep(1)
        logger.info("Initialization wait complete")

    async def start_scan(self) -> str:
        """Send start command. Scanner must be configured first."""
        return await self._scan_command("start")

    async def stop_scan(self) -> str:
        """Send stop command (finishes scan, saves files)."""
        return await self._scan_command("stop")

    async def pause_scan(self) -> str:
        """Send pause command."""
        return await self._scan_command("pause")

    async def cancel_scan(self) -> str:
        """Send cancel command (aborts scan, no save)."""
        return await self._scan_command("cancel")

    async def _scan_command(self, action: str) -> str:
        """Send a command.pl?action=<action> request.

        On success: returns 302 redirect to console.pl.
        On error (not configured): returns 200 with XML-like error:
            <Error shorterror="Not configured">Scanner not configured.</Error>
        """
        session = self._check_session()
        url = f"{self._base_url}{_SCAN_BASE}/command.pl"
        params = {"action": action}
        logger.info("Scan command: %s", action)

        async with session.get(url, params=params, allow_redirects=False) as resp:
            body = await resp.text()
            logger.info("command.pl?action=%s → HTTP %d, body: %s",
                        action, resp.status, body[:300])
            if resp.status == 302:
                return body
            if "<Error" in body or "not configured" in body.lower():
                raise OdysseyScanError(
                    f"Scan command '{action}' failed: {body[:500]}"
                )
            return body

    async def estimate_scan_time(self, params: ScanParameters) -> str:
        """Get estimated scan time from time.pl.

        Returns the time string, e.g. "0 hours 2 minutes 15 seconds".
        """
        session = self._check_session()
        url = f"{self._base_url}{_SCAN_BASE}/time.pl"
        async with session.get(url, params=params.to_time_params()) as resp:
            html = await resp.text()
            match = re.search(
                r"Estimated Scan Time.*?(\d+ hours? \d+ minutes? \d+ seconds?)",
                html, re.DOTALL | re.IGNORECASE,
            )
            return match.group(1) if match else html

    # -- Status --------------------------------------------------------------

    async def get_status(self) -> dict[str, str]:
        """Fetch and parse the instrument status page.

        Returns dict with keys: state, current_user, progress,
        time_remaining, lid_status. The most recent raw HTML response
        is cached on ``self._last_status_html`` so a diagnostic endpoint
        can surface it without making a second round-trip; this is
        what the lab app's ``/api/diagnostics/status`` reads.
        """
        session = self._check_session()
        url = f"{self._base_url}{_STATUS_BASE}/"
        async with session.get(url) as resp:
            html = await resp.text()
            self._last_status_http = resp.status
        self._last_status_html = html
        parsed = self._parse_status_html(html)
        if parsed["state"] == "Unknown":
            logger.warning(
                "Status parser missed 'Scanner Status' (HTTP %s, %d bytes). "
                "Open /api/diagnostics/status in the app for the raw HTML.",
                self._last_status_http, len(html),
            )
        return parsed

    async def stop_from_status(self) -> str:
        """Stop the scanner from the status/utilities page.

        This is the way to release a paused/stuck scanner without
        going through the scan console.
        """
        session = self._check_session()
        url = f"{self._base_url}{_STATUS_BASE}/status"
        data = {"formContext": "1", "action": "Stop"}
        async with session.post(url, data=data) as resp:
            return await resp.text()

    async def get_scan_progress(
        self, scan_name: str, group: str
    ) -> dict[str, str]:
        """Fetch scan progress from the imaging info panel.

        Returns dict with: dimensions, file_size, time_left, progress.
        """
        session = self._check_session()
        url = f"{self._base_url}{_IMAGE_BASE}/info.pl"
        params = {
            "scan": scan_name,
            "group": group,
            "update": "Off",
            "console": "yes",
        }
        async with session.get(url, params=params) as resp:
            html = await resp.text()
        return self._parse_info_html(html)

    # -- Image retrieval -----------------------------------------------------

    async def download_tiff(
        self, group: str, scan_name: str, channel: int
    ) -> bytes:
        """Download a raw TIFF file for one channel (700 or 800).

        URL: /scan/image/<name>-<channel>.tif?xml=<encoded-xml>

        Retries up to 3× on transient connection errors with a 0.25 s
        backoff (Pattern 4). HTTP 4xx/5xx fail immediately. Verifies the
        downloaded byte count matches the server's Content-Length when
        present, so partial reads don't masquerade as success.
        """
        session = self._check_session()
        xml = _tiff_xml(group, scan_name, channel)
        url = (
            f"{self._base_url}/scan/image/"
            f"{quote(scan_name)}-{channel}.tif"
        )
        logger.info("Downloading TIFF: %s channel %d", scan_name, channel)

        last_exc: Optional[Exception] = None
        for attempt in range(_HTTP_RETRY_ATTEMPTS):
            try:
                async with session.get(url, params={"xml": xml}) as resp:
                    if resp.status != 200:
                        # Server gave a real answer — don't retry.
                        raise OdysseyImageError(
                            f"TIFF download failed for {scan_name}-{channel}: "
                            f"HTTP {resp.status}"
                        )
                    expected = resp.content_length  # may be None
                    data = await resp.read()
                    if expected is not None and len(data) != expected:
                        # Partial read — treat as transient and retry.
                        raise IOError(
                            f"Truncated TIFF: got {len(data)} bytes, "
                            f"expected {expected} (Content-Length)"
                        )
                    logger.info(
                        "Downloaded %s-%d.tif: %d bytes",
                        scan_name, channel, len(data),
                    )
                    return data
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                if attempt < _HTTP_RETRY_ATTEMPTS - 1:
                    logger.warning(
                        "TIFF download attempt %d/%d for %s-%d failed (%s) — retrying",
                        attempt + 1, _HTTP_RETRY_ATTEMPTS,
                        scan_name, channel, exc,
                    )
                    await asyncio.sleep(_HTTP_RETRY_DELAY)
                    continue
        raise OdysseyImageError(
            f"TIFF download for {scan_name}-{channel} failed after "
            f"{_HTTP_RETRY_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc

    async def get_jpeg_preview(
        self,
        group: str,
        scan_name: str,
        contrast_700: int = 5,
        contrast_800: int = 5,
        channels: str = "700 800",
        background: str = "black",
    ) -> bytes:
        """Fetch a JPEG preview image with display settings applied.

        The instrument renders the preview server-side with the specified
        contrast, channel selection, and background color.
        """
        session = self._check_session()
        xml = _jpeg_xml(
            group, scan_name,
            contrast_700=contrast_700,
            contrast_800=contrast_800,
            channels=channels,
            background=background,
        )
        url = f"{self._base_url}/scan/image"
        async with session.get(url, params={"xml": xml}) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"JPEG preview failed: HTTP {resp.status}"
                )
            return await resp.read()

    async def download_scan_log(self, group: str, scan_name: str) -> str:
        """Download the scan log for a completed scan."""
        session = self._check_session()
        url = f"{self._base_url}{_IMAGE_BASE}/savelog.pl"
        params = {"group": group, "scan": scan_name}
        async with session.get(url, params=params) as resp:
            return await resp.text()

    async def list_scan_groups(self) -> str:
        """Fetch the scan setup page HTML (contains group dropdown).

        Parse the HTML to extract available group names from the
        <select name="avail"> element.
        """
        session = self._check_session()
        url = f"{self._base_url}{_SCAN_BASE}/"
        async with session.get(url) as resp:
            return await resp.text()

    # -- Utilities -----------------------------------------------------------

    async def shutdown_instrument(self) -> str:
        """Send shutdown command. Requires Administrator access.

        WARNING: This powers off the instrument. It will take up to
        30 minutes to restart.
        """
        session = self._check_session()
        url = f"{self._base_url}/scanapp/admin/admin/index"
        params = {"action": "InitiateShutdown"}
        logger.warning("Shutting down Odyssey instrument")
        async with session.get(url, params=params) as resp:
            return await resp.text()

    async def get_instrument_info(self) -> str:
        """Fetch instrument info (serial number, software version, etc.)."""
        session = self._check_session()
        url = f"{self._base_url}/scanapp/help/instinfo.pl"
        async with session.get(url) as resp:
            return await resp.text()

    # -- Raw request (for discovery/debugging) -------------------------------

    async def get(self, path: str, **kwargs: Any) -> str:
        """Raw GET request to any path on the instrument."""
        session = self._check_session()
        url = f"{self._base_url}{path}"
        async with session.get(url, **kwargs) as resp:
            return await resp.text()

    async def post(self, path: str, data: dict, **kwargs: Any) -> str:
        """Raw POST request to any path on the instrument."""
        session = self._check_session()
        url = f"{self._base_url}{path}"
        async with session.post(url, data=data, **kwargs) as resp:
            return await resp.text()

    # -- HTML parsers --------------------------------------------------------

    @staticmethod
    def _parse_status_html(html: str) -> dict[str, str]:
        """Parse the instrument status page HTML.

        Robust to tag layout: finds the label anywhere in the HTML
        (case-insensitive), then walks forward skipping any tags and
        whitespace until it hits the first non-empty text run. Handles
        all three known shapes:

        - Plain text:        ``Scanner Status: Idle``
        - Tags between
          label and colon:   ``Scanner Status</b>: Idle``
        - Table layout:      ``<td>Scanner Status</td><td>Idle</td>``
        """
        def _extract(label: str) -> str:
            idx = html.lower().find(label.lower())
            if idx < 0:
                return ""
            rest = html[idx + len(label):]
            # Optional colon between label and value, possibly preceded
            # by closing tags (e.g. ``Scanner Status</b>:``).
            rest = re.sub(r'^(?:\s|<[^>]+>)*:?', '', rest, count=1)
            # Replace every tag with a separator and pick the first
            # non-empty text fragment.
            text = re.sub(r'<[^>]+>', '|', rest)
            for fragment in text.split('|'):
                trimmed = fragment.strip()
                if trimmed:
                    # Stop at a newline so we don't bleed into the next row.
                    return trimmed.split('\n')[0].strip()
            return ""

        return {
            "state": _extract("Scanner Status") or "Unknown",
            "current_user": _extract("Current User"),
            "progress": _extract("Percent Complete"),
            "time_remaining": _extract("Time Remaining"),
            "lid_status": _extract("Lid Status"),
        }

    @staticmethod
    def _parse_info_html(html: str) -> dict[str, str]:
        """Parse the imaging info panel HTML."""
        def _extract(label: str) -> str:
            pattern = rf'{label}\s*[:]\s*(?:<[^>]+>)*\s*([^<\n]+)'
            match = re.search(pattern, html, re.IGNORECASE)
            return match.group(1).strip() if match else ""

        return {
            "dimensions": _extract("Dimensions"),
            "file_size": _extract("File Size"),
            "time_left": _extract("Time Left"),
        }

    @staticmethod
    def parse_select_options(html: str, select_name: str) -> list[str]:
        """Extract <option> values from an HTML <select> by name."""
        pattern = (
            rf'<select[^>]*name=["\']?{select_name}["\']?[^>]*>'
            r'(.*?)</select>'
        )
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if not match:
            return []
        return re.findall(
            r'<option[^>]*value=["\']?([^"\'>\s]+)',
            match.group(1),
            re.IGNORECASE,
        )
