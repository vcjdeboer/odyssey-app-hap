"""Odyssey Western Blot GUI — FastAPI backend.

Serves the web UI and provides API endpoints for scan control,
metadata capture, image retrieval, and image processing. Uses the
PLR Odyssey driver for instrument communication (simulated by default,
connects to real hardware when ODYSSEY_HOST is set).

Run with:
    uvicorn odyssey_app.app:app --reload --port 8000

    # With real instrument:
    ODYSSEY_HOST=169.254.206.190 ODYSSEY_PASS=odyssey uvicorn odyssey_app.app:app --port 8000

Then open: http://localhost:8000
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, Response

from odyssey_app.metadata import WesternBlotRecord
from odyssey_app.instrument import ODYSSEY_INSTANCE_CARD

# Conditionally import PLR + image libraries
try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from plr_v4.odyssey.connection import (
        OdysseyDriver, ScanParameters, DEFAULT_GROUP,
    )
    from plr_v4.odyssey.status_backend import normalize_state, InstrumentState
    from plr_v4.odyssey.device_card import ODYSSEY_CLASSIC_BASE
    from plr_v4.odyssey.tagging import (
        build_identity_description,
        tag_tiff_with_identity,
    )
    from plr_v4.capabilities.scanning import Scanning
    from plr_v4.capabilities.image_retrieval import ImageRetrieval
    from plr_v4.capabilities.instrument_status import InstrumentStatus
    from plr_v4.odyssey.simulated import (
        OdysseyState,
        OdysseyScanSimulated,
        OdysseyImageSimulated,
        OdysseyStatusSimulated,
    )
    PLR_AVAILABLE = True
    # Merged effective card for THIS lab's unit (model-base + instance).
    # Read by the API endpoints to populate identity in TIFFs/JSON/UI.
    _DEVICE_CARD = ODYSSEY_CLASSIC_BASE.merge(ODYSSEY_INSTANCE_CARD)
except ImportError:
    PLR_AVAILABLE = False
    DEFAULT_GROUP = "odyssey"
    _DEVICE_CARD = None
    def normalize_state(raw: str) -> str:
        return raw or "Idle"
    def build_identity_description(*a, **kw): return "{}"
    def tag_tiff_with_identity(raw, *a, **kw): return raw

try:
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


app = FastAPI(title="Odyssey Western Blot Imager")

# -- Config --
STATIC_DIR = Path(__file__).parent / "static"
RECORDS_DIR = Path(__file__).parent / "records"
SCANS_DIR = Path(__file__).parent / "scans"
EXPORTS_DIR = Path(__file__).parent / "exports"  # per-experiment attachments
RECORDS_DIR.mkdir(exist_ok=True)
SCANS_DIR.mkdir(exist_ok=True)
EXPORTS_DIR.mkdir(exist_ok=True)


def _safe(s: str, fallback: str = "x") -> str:
    """Make a string safe for use as a filesystem segment."""
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in (s or ""))
    return cleaned or fallback


def _identity() -> dict:
    """Return the active card's identity dict (or empty dict if no card)."""
    return dict((_DEVICE_CARD.identity if _DEVICE_CARD is not None else {}) or {})


def _tag_tiff(
    raw_bytes: bytes,
    *,
    scan_name: str = "",
    channel: Optional[int] = None,
) -> bytes:
    """Thin app-side wrapper: stamp the active card's identity into a TIFF."""
    if _DEVICE_CARD is None:
        return raw_bytes
    return tag_tiff_with_identity(
        raw_bytes, _DEVICE_CARD,
        scan_name=scan_name, channel=channel,
        software_tag="Odyssey Western Blot Imager (PLR_v4)",
    )


def _identity_description(scan_name: str = "", channel: Optional[int] = None) -> str:
    """Thin app-side wrapper: identity JSON blob for TIFF tag 270."""
    if _DEVICE_CARD is None:
        return "{}"
    return build_identity_description(
        _DEVICE_CARD, scan_name=scan_name, channel=channel,
    )


def _list_attachments(experiment_id: str, scan_name: str) -> list[str]:
    """List attachment filenames for a given (experiment_id, scan_name)."""
    if not experiment_id or not scan_name:
        return []
    exp_dir = EXPORTS_DIR / _safe(experiment_id)
    if not exp_dir.exists():
        return []
    prefix = _safe(scan_name) + "__"
    return sorted(f.name for f in exp_dir.glob(f"{prefix}*"))

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# -- State --
_current_record: Optional[WesternBlotRecord] = None
_scan_state: dict = {
    "state": "Idle",
    "progress": 0,
    "time_remaining": "",
    "lid_open": False,
    "current_user": "",
    "current_scan": "",
    "current_group": "",
}
_image_state: dict = {
    "loaded": False,
    "scan_name": "",
    "group": "",
    "brightness": 0,
    "contrast": 0,
    "channel_700": True,
    "channel_800": True,
    "inverted": False,
    "crop": None,  # {x0, y0, x1, y1} or None
}
_ws_clients: list[WebSocket] = []

import logging
logging.basicConfig(level=logging.INFO)

# -- PLR driver (simulated or real) --
_odyssey_driver = None
_sim_state = None
_scan_driver = None
_image_driver = None
_status_driver = None


@app.on_event("startup")
async def maybe_open_browser():
    """When the launcher sets ``ODYSSEY_OPEN_BROWSER=1`` (run.bat in the
    lab deploy), pop the UI in the default browser as soon as uvicorn
    is ready. Skipped during dev so reload iterations don't keep
    spawning tabs.
    """
    if os.environ.get("ODYSSEY_OPEN_BROWSER", "").strip() in ("", "0", "false", "False"):
        return
    import asyncio
    import webbrowser

    async def _open():
        # Tiny delay so uvicorn has finished binding the port and the
        # lifespan startup is past — otherwise the browser hits the
        # window between bind and accept and shows a bare error page.
        await asyncio.sleep(1.5)
        webbrowser.open("http://localhost:8000")

    asyncio.create_task(_open())


@app.on_event("startup")
async def startup():
    """Initialize PLR driver — real or simulated.

    FR-027: credentials come from ODYSSEY_USER/ODYSSEY_PASS env vars via
    OdysseyDriver.from_env(). If ODYSSEY_HOST is set but credentials
    are missing, we fail loudly with a log message and fall back to
    simulated mode rather than silently connecting with defaults.
    """
    global _odyssey_driver, _sim_state
    global _scan_driver, _image_driver, _status_driver

    if not PLR_AVAILABLE:
        return

    host = os.environ.get("ODYSSEY_HOST", "")
    if host:
        # Real instrument connection (FR-027)
        try:
            _odyssey_driver = OdysseyDriver.from_env(host=host)
        except ValueError as e:
            logging.warning(
                "Real-hardware mode requested (ODYSSEY_HOST=%s) but "
                "credentials are missing: %s. Falling back to simulated mode.",
                host, e,
            )
            _setup_simulated()
            return
        try:
            await _odyssey_driver.setup()
            from plr_v4.odyssey.scan_backend import OdysseyScanBackend
            from plr_v4.odyssey.image_backend import OdysseyImageBackend
            from plr_v4.odyssey.status_backend import OdysseyStatusBackend
            _scan_driver = Scanning(backend=OdysseyScanBackend(_odyssey_driver))
            _image_driver = ImageRetrieval(backend=OdysseyImageBackend(_odyssey_driver))
            _status_driver = InstrumentStatus(
                backend=OdysseyStatusBackend(_odyssey_driver)
            )
            await _scan_driver.setup()
            _scan_driver._setup_finished = True
            await _image_driver.setup()
            _image_driver._setup_finished = True
            await _status_driver.setup()
            _status_driver._setup_finished = True
        except Exception as e:
            print(f"WARNING: Could not connect to Odyssey at {host}: {e}")
            print("Falling back to simulated mode.")
            _odyssey_driver = None
            _setup_simulated()
    else:
        _setup_simulated()


def _setup_simulated():
    """Set up simulated PLR backends wrapped in capability frontends."""
    global _sim_state, _scan_driver, _image_driver, _status_driver
    _sim_state = OdysseyState()
    _scan_driver = Scanning(backend=OdysseyScanSimulated(_sim_state))
    _image_driver = ImageRetrieval(backend=OdysseyImageSimulated(_sim_state))
    _status_driver = InstrumentStatus(
        backend=OdysseyStatusSimulated(_sim_state)
    )
    _scan_driver._setup_finished = True
    _image_driver._setup_finished = True
    _status_driver._setup_finished = True


@app.on_event("shutdown")
async def shutdown():
    """Close PLR connection."""
    if _odyssey_driver is not None:
        await _odyssey_driver.stop()


# -- WebSocket for live updates --

async def _broadcast(msg: dict):
    """Send a message to all connected WebSocket clients."""
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        # Send current state immediately
        await ws.send_json({"type": "status", **_scan_state})
        await ws.send_json({"type": "image_state", **_image_state})
        # Keep alive — listen for client messages
        while True:
            data = await ws.receive_text()
            # Client can request status refresh
            if data == "refresh":
                await ws.send_json({"type": "status", **_scan_state})
    except WebSocketDisconnect:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


# -- Pages --

@app.get("/")
async def index():
    # Stream the bytes verbatim with an explicit UTF-8 charset. Going
    # through ``read_text()`` decodes with the platform default — which
    # is cp1252 on Windows — and mangles every non-ASCII glyph in the
    # page (em-dash, ``×``, ``µ``, the lock emoji…) before the response
    # ever leaves the server.
    #
    # Cache-Control: no-store forces Chrome to re-fetch on every page
    # load. Without it, after a uvicorn restart the browser can keep
    # serving the previous session's HTML+JS and the UI gets stuck
    # against a server that has different routes — Configure clicks go
    # to handlers that no longer exist, status updates don't arrive,
    # and the user has to hard-refresh to recover. This is the pattern
    # Vincent kept hitting in the lab.
    return Response(
        (STATIC_DIR / "index.html").read_bytes(),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


# -- Scan API --

@app.post("/api/scan/configure")
async def configure_scan(data: dict):
    """Configure a scan with parameters from the GUI.

    FR-018: scan_group must be 'odyssey'. This is enforced server-side
    so that direct API clients (curl, tests, dev-tools) cannot bypass
    the GUI's locked-group field.
    """
    global _scan_state
    # In real-hardware mode, refuse to "succeed" before the startup hook
    # has finished standing up the driver. Without this gate, the
    # browser auto-launched by run.bat could fire Configure during the
    # 1-3 s window where uvicorn has bound the port but the Odyssey
    # session isn't ready yet — the handler used to fall through
    # silently (no scanner sound) but happily flipped state to
    # "Configured", forcing the user to refresh to recover.
    if PLR_AVAILABLE and os.environ.get("ODYSSEY_HOST", "") and _scan_driver is None:
        raise HTTPException(
            status_code=503,
            detail="Instrument is still initializing — try again in a couple of seconds.",
        )
    group = data.get("scan_group", DEFAULT_GROUP)
    if group != DEFAULT_GROUP:
        raise HTTPException(
            status_code=400,
            detail=f"scan_group must be '{DEFAULT_GROUP}' on this instrument",
        )
    try:
        scan_settings = data.get("scan_settings", {})
        params = ScanParameters(
            name=data.get("scan_name", "scan"),
            group=group,
            resolution=str(scan_settings.get("resolution_um", 169)),
            quality=scan_settings.get("quality", "medium"),
            intensity_700=str(scan_settings.get("intensity_700", 5)),
            intensity_800=str(scan_settings.get("intensity_800", 5)),
            channel_700=scan_settings.get("channel_700", True),
            channel_800=scan_settings.get("channel_800", True),
            origin_x=scan_settings.get("origin_x", 0),
            origin_y=scan_settings.get("origin_y", 0),
            width=scan_settings.get("width_cm", 10),
            height=scan_settings.get("height_cm", 10),
            focus=scan_settings.get("focus_offset_mm", 0.0),
            comment=data.get("notes", ""),
        )

        if _scan_driver:
            await _scan_driver.configure(params)

        _scan_state["state"] = "Configured"
        _scan_state["progress"] = 0  # reset from any previous scan's 100%
        _scan_state["time_remaining"] = ""
        _scan_state["current_scan"] = params.name
        _scan_state["current_group"] = params.group
        await _broadcast({"type": "status", **_scan_state})
        return {"status": "ok", "message": "Scan configured"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# Terminal states. 'Idle' is included because real Odyssey hardware
# returns to Idle when a scan finishes — it does NOT use 'Completed'
# (which is a simulator convention). The fresh-terminal-state guard
# in _poll_scan_progress prevents a stale-Idle from firing scan_complete
# spuriously: we only accept Idle as 'this scan finished' AFTER we've
# observed the instrument in a non-terminal state (Scanning, etc.).
# See docs/odyssey-http-patterns.md Pattern 1.
_TERMINAL_STATES = {"Idle", "Completed", "Stopped", "Failed"}


async def _emit_scan_complete(outcome: str) -> None:
    """Broadcast exactly one scan_complete message.

    ``outcome`` is the terminal state in lowercase, per the WS contract
    (see specs/022-odyssey-app-lab-fixes/contracts/odyssey-app-http-api.md).
    """
    await _broadcast({
        "type": "scan_complete",
        "scan": _scan_state.get("current_scan", ""),
        "group": _scan_state.get("current_group", DEFAULT_GROUP),
        "outcome": outcome.lower(),
        "channels_available": ["700", "800"],
    })


@app.post("/api/scan/start")
async def start_scan():
    """Start a configured scan.

    Broadcasts Scanning/0% BEFORE launching the driver so the progress bar
    resets immediately and doesn't show the previous scan's 100% during
    the new run. Both sim and real modes run as background tasks so the
    HTTP response returns promptly.
    """
    global _scan_state
    try:
        # Immediate visible reset — before any blocking await.
        _scan_state["state"] = "Scanning"
        _scan_state["progress"] = 0
        _scan_state["time_remaining"] = ""
        await _broadcast({"type": "status", **_scan_state})

        if _sim_state is not None:
            asyncio.create_task(_run_sim_scan())
        elif _status_driver and _odyssey_driver:
            # Real: fire the start command, then poll for progress.
            await _scan_driver.start()
            asyncio.create_task(_poll_scan_progress())

        return {"status": "ok", "message": "Scan started"}
    except Exception as e:
        _scan_state["state"] = "Failed"
        await _broadcast({"type": "status", **_scan_state})
        raise HTTPException(status_code=400, detail=str(e))


async def _run_sim_scan():
    """Run the simulated scan and broadcast progress every 100 ms.

    Runs the sim's start() as a background coroutine while this loop
    polls the sim status and broadcasts. When the sim reaches a terminal
    state, emits exactly one scan_complete.
    """
    global _scan_state
    sim_task = asyncio.create_task(_scan_driver.start())
    try:
        while not sim_task.done():
            try:
                status = await _status_driver.read()
                _scan_state["state"] = normalize_state(status.state)
                _scan_state["progress"] = status.progress
                await _broadcast({"type": "status", **_scan_state})
            except Exception as e:
                logging.warning("Sim poll error: %s", e)
            await asyncio.sleep(0.1)
        # Drain any exception from sim_task and pick up the final state.
        try:
            await sim_task
        except Exception as e:
            logging.error("Sim scan failed: %s", e)
            _scan_state["state"] = "Failed"
            await _broadcast({"type": "status", **_scan_state})
            await _emit_scan_complete("failed")
            return
        final = await _status_driver.read()
        _scan_state["state"] = normalize_state(final.state)
        _scan_state["progress"] = final.progress
        await _broadcast({"type": "status", **_scan_state})
        await _emit_scan_complete(_scan_state["state"])
    except Exception as e:
        logging.error("Unexpected error in _run_sim_scan: %s", e, exc_info=True)
        _scan_state["state"] = "Failed"
        await _broadcast({"type": "status", **_scan_state})
        await _emit_scan_complete("failed")


async def _poll_scan_progress():
    """Background task: poll Odyssey status until the scan reaches a *fresh*
    terminal state.

    Implements the stale-terminal-state guard (Pattern 1 from
    docs/odyssey-http-patterns.md). Without it, this poller could fire
    instantly with the previous scan's terminal state still in place
    and emit a bogus scan_complete. With it, we capture the initial
    state and require a transition out of any terminal before the next
    terminal observation counts as 'this scan is done'.
    """
    global _scan_state
    logging.info("Starting scan progress polling...")
    emitted = False

    # Capture initial state. If it was already terminal, demand a
    # state change before we accept the next terminal as fresh.
    try:
        initial = await _status_driver.read()
        require_state_change = (
            normalize_state(initial.state) in _TERMINAL_STATES
        )
    except Exception:
        require_state_change = False  # Read fails will be caught in the loop.

    while True:
        await asyncio.sleep(3)
        try:
            status = await _status_driver.read()
            state = normalize_state(status.state)
            _scan_state["state"] = state
            _scan_state["progress"] = status.progress
            _scan_state["time_remaining"] = status.time_remaining
            _scan_state["lid_open"] = status.lid_open
            await _broadcast({"type": "status", **_scan_state})

            if state not in _TERMINAL_STATES:
                require_state_change = False  # saw a transition — next terminal is fresh
                continue

            if require_state_change:
                # Stale terminal — keep polling until the instrument
                # actually moves out of this state.
                continue

            if not emitted:
                # Real hw signals scan-finished by going back to Idle;
                # sim signals it as Completed. Both should fire as
                # outcome='completed' so the GUI's onScanComplete path
                # (which auto-loads the image) treats them identically.
                if state in {"Idle", "Completed"}:
                    _scan_state["progress"] = 100
                    await _broadcast({"type": "status", **_scan_state})
                outcome = "completed" if state in {"Idle", "Completed"} else state
                await _emit_scan_complete(outcome)
                emitted = True
                return
        except Exception as e:
            logging.error("Poll error: %s", e, exc_info=True)
            # Treat poll crash as a terminal failure so the UI unblocks.
            _scan_state["state"] = "Failed"
            await _broadcast({"type": "status", **_scan_state})
            if not emitted:
                await _emit_scan_complete("failed")
            return


@app.post("/api/scan/stop")
async def stop_scan():
    """Stop the current scan — saves partial image (FR-021).

    Returns {"status": "stopped", "partial": bool, "channels_available": [...]}.
    Against the real instrument this invokes driver.backend.stop_and_save();
    against the simulator it calls backend.stop() and derives the partial
    flag from OdysseyState.stop_was_partial.
    """
    global _scan_state
    try:
        partial = False
        channels_available: list[str] = []
        if _scan_driver:
            backend = _scan_driver.backend
            if hasattr(backend, "stop_and_save"):
                result = await backend.stop_and_save()
                partial = bool(result.partial)
                channels_available = [str(c) for c in result.channels_available]
            else:
                # Simulated path
                await _scan_driver.stop_scan()
                if _sim_state is not None:
                    partial = bool(_sim_state.stop_was_partial)
                    if partial:
                        channels_available = ["700", "800"]

        _scan_state["state"] = "Stopped"
        _scan_state["progress"] = 0
        await _broadcast({"type": "status", **_scan_state})
        await _emit_scan_complete("stopped")

        return {
            "status": "stopped",
            "partial": partial,
            "channels_available": channels_available,
        }
    except Exception as e:
        logging.error("Stop error: %s", e, exc_info=True)
        _scan_state["state"] = "Failed"
        await _broadcast({"type": "status", **_scan_state})
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scan/pause")
async def pause_scan():
    """Pause the current scan (instrument stays locked!)."""
    global _scan_state
    try:
        if _scan_driver:
            await _scan_driver.pause()
        _scan_state["state"] = "Paused"
        await _broadcast({"type": "status", **_scan_state})
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/scan/cancel")
async def cancel_scan():
    """Cancel the current scan (no save, no partial image)."""
    global _scan_state
    try:
        if _scan_driver:
            await _scan_driver.cancel()
        _scan_state["state"] = "Idle"
        _scan_state["progress"] = 0
        await _broadcast({"type": "status", **_scan_state})
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/scan/status")
async def scan_status():
    """Get current scan status."""
    if _status_driver and _odyssey_driver:
        try:
            status = await _status_driver.read()
            _scan_state["state"] = status.state
            _scan_state["progress"] = status.progress
            _scan_state["time_remaining"] = status.time_remaining
            _scan_state["lid_open"] = status.lid_open
            _scan_state["current_user"] = status.current_user
        except Exception:
            pass
    return _scan_state


@app.get("/api/scan/estimate")
async def estimate_time(
    resolution: str = "169", quality: str = "medium",
    x0: int = 0, y0: int = 0, width: int = 10, height: int = 10,
):
    """Estimate scan time without configuring."""
    if _scan_driver and _odyssey_driver and hasattr(_scan_driver.backend, "estimate_time"):
        params = ScanParameters(
            resolution=resolution, quality=quality,
            origin_x=x0, origin_y=y0, width=width, height=height,
        )
        time_str = await _scan_driver.backend.estimate_time(params)
        return {"estimate": time_str}
    return {"estimate": "Not available in simulated mode"}


# -- Image API --

async def _render_single_channel(group: str, scan: str, channel: int) -> bytes:
    """Render one channel to PNG bytes.

    Real mode: fetch the server-side JPEG preview for this channel only.
    Simulated mode: generate a placeholder coloured per channel.
    Returns empty bytes if the channel is not available.
    """
    if _image_driver and _odyssey_driver and hasattr(
        _image_driver.backend, "get_preview"
    ):
        try:
            return await _image_driver.backend.get_preview(
                group, scan,
                contrast_700=5, contrast_800=5,
                channels=str(channel),
                background="black",
            )
        except Exception as e:
            logging.info("Channel %d not available: %s", channel, e)
            return b""
    # Simulated
    if not PIL_AVAILABLE:
        return b""
    color = (255, 100, 100) if channel == 700 else (100, 255, 100)
    img = Image.new("RGB", (400, 300), (10, 10, 20))
    draw = ImageDraw.Draw(img)
    import random
    random.seed(channel)
    for lane in range(6):
        x = 50 + lane * 55
        for band in range(3):
            y = 60 + band * 70 + random.randint(-10, 10)
            w = 30 + random.randint(-5, 5)
            h = 8 + random.randint(-2, 4)
            intensity = random.randint(80, 255)
            shade = tuple(min(255, int(c * intensity / 255)) for c in color)
            draw.ellipse([(x, y), (x + w, y + h)], fill=shade)
    draw.text((150, 280), f"Simulated {channel}nm", fill=(60, 60, 60))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@app.get("/api/image/channels")
async def get_channels(scan: str, group: str = DEFAULT_GROUP):
    """Return both channel images in one response (FR-005).

    The browser calls this exactly once per scan_complete event and caches
    the bytes as ImageBitmaps — subsequent view switches (700 / 800 /
    Overlay) are served from the local cache with zero server round-trips
    (SC-003).

    Response envelope per specs/022-odyssey-app-lab-fixes/contracts/
    odyssey-app-http-api.md: {scan, group, ch700, ch800, fetched_at}.
    Each channel is either {"format": "image/png", "bytes_base64": "..."}
    or null if unavailable.
    """
    if group != DEFAULT_GROUP:
        raise HTTPException(
            status_code=400,
            detail=f"scan_group must be '{DEFAULT_GROUP}' on this instrument",
        )
    if not scan:
        raise HTTPException(status_code=400, detail="scan parameter required")

    ch700_bytes = await _render_single_channel(group, scan, 700)
    ch800_bytes = await _render_single_channel(group, scan, 800)

    def _encode(b: bytes) -> Optional[dict]:
        if not b:
            return None
        return {
            "format": "image/png",
            "bytes_base64": base64.b64encode(b).decode("ascii"),
        }

    return {
        "scan": scan,
        "group": group,
        "ch700": _encode(ch700_bytes),
        "ch800": _encode(ch800_bytes),
        "fetched_at": datetime.now().isoformat(),
    }


@app.get("/api/image/preview")
async def get_preview(
    group: str = "public", scan: str = "",
    contrast700: int = 5, contrast800: int = 5,
    channels: str = "700 800", background: str = "black",
):
    """Get a JPEG preview from the instrument."""
    if _image_driver and _odyssey_driver and hasattr(_image_driver.backend, "get_preview"):
        try:
            jpeg_bytes = await _image_driver.backend.get_preview(
                group, scan,
                contrast_700=contrast700,
                contrast_800=contrast800,
                channels=channels,
                background=background,
            )
            return Response(content=jpeg_bytes, media_type="image/jpeg")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    # Simulated: return a placeholder
    return _generate_placeholder_image()


@app.get("/api/image/tiff/{channel}")
async def download_tiff(
    channel: int, group: str = "public", scan: str = "",
):
    """Download raw TIFF for one channel (700 or 800)."""
    if _image_driver and _odyssey_driver and hasattr(_image_driver.backend, "download_channel"):
        try:
            tiff_bytes = await _image_driver.backend.download_channel(group, scan, channel)
            tiff_bytes = _tag_tiff(
                tiff_bytes, scan_name=scan, channel=channel,
            )
            return Response(
                content=tiff_bytes,
                media_type="image/tiff",
                headers={"Content-Disposition": f"attachment; filename={scan}-{channel}.tif"},
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=404, detail="Not available in simulated mode")


@app.post("/api/image/export")
async def export_image(data: dict):
    """Render an image export and ATTACH it to the current experiment.

    Behaviour changed in Phase 29: previously this returned the rendered
    bytes as a browser download. Now it writes the file to
    ``odyssey_app/exports/<experiment_id>/<scan>__<variant>.<ext>`` so
    the 'Export experiment ZIP' can bundle every attachment alongside
    the scan records and raw images. The response is a small JSON
    confirmation, not a blob.

    Required fields: experiment_id, scan_name, format, view.
    Optional: include_footer. Accepts flat payload (FR-017 — no
    circular metadata); any legacy nested 'metadata' key is ignored.
    """
    if not PIL_AVAILABLE:
        raise HTTPException(status_code=500, detail="Pillow not installed")

    scan_name = (data.get("scan_name") or "").strip()
    experiment_id = (data.get("experiment_id") or "").strip()
    include_footer = data.get("include_footer", True)
    format = data.get("format", "png")
    view = data.get("view", "700")
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else data

    if not experiment_id:
        raise HTTPException(status_code=400, detail="experiment_id is required")
    if not scan_name:
        raise HTTPException(status_code=400, detail="scan_name is required")

    # TODO: Load real TIFF data from scans directory, apply display settings
    # For now generate a placeholder
    width, height = 400, 300
    footer_height = 80 if include_footer else 0
    total_height = height + footer_height

    img = Image.new("RGB", (width, total_height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Simulated bands
    draw.text((width // 2 - 40, height // 2 - 10), "Scan Data", fill=(100, 100, 100))

    if include_footer and metadata:
        # Draw metadata footer
        draw.rectangle([(0, height), (width, total_height)], fill=(30, 30, 30))
        y = height + 5
        font_size = 10
        antibodies = metadata.get("primary_antibodies", [])
        for ab in antibodies[:2]:
            target = ab.get("target", "")
            vendor = ab.get("vendor", "")
            catalog = ab.get("catalog", "")
            dilution = ab.get("dilution", "")
            ch = ab.get("channel", "")
            if target:
                color = (255, 100, 100) if ch == 700 else (100, 255, 100)
                text = f"{ch}nm: {target} ({vendor} {catalog}, {dilution})"
                draw.text((5, y), text, fill=color)
                y += 14

        settings = metadata.get("scan_settings", {})
        res = settings.get("resolution_um", "")
        operator = metadata.get("operator", "")
        exp_id = metadata.get("experiment_id", "")
        info_line = f"Res: {res}um  {operator}  {exp_id}"
        draw.text((5, y), info_line, fill=(150, 150, 150))

    buf = io.BytesIO()
    if format == "tiff":
        img.save(buf, format="TIFF", tiffinfo={
            270: _identity_description(scan_name=scan_name),
            305: "Odyssey Western Blot Imager (PLR_v4)",
        })
        ext = "tif"
    else:
        # PNG: stash PID in tEXt chunks via PngInfo.
        from PIL.PngImagePlugin import PngInfo
        pnginfo = PngInfo()
        for k, v in _identity().items():
            pnginfo.add_text(f"instrument_{k}", str(v))
        if scan_name:
            pnginfo.add_text("scan_name", scan_name)
        img.save(buf, format="PNG", pnginfo=pnginfo)
        ext = "png"
    buf.seek(0)
    blob = buf.getvalue()

    # Variant label: combine format + footer choice + active view so the
    # user's three intents (PNG+footer / PNG clean / TIFF+metadata) each
    # land as distinct files under the scan.
    variant_parts = [view or "view"]
    if format == "tiff":
        variant_parts.append("tiff")
    elif include_footer:
        variant_parts.append("png-footer")
    else:
        variant_parts.append("png-clean")
    variant = _safe("-".join(variant_parts))

    exp_dir = EXPORTS_DIR / _safe(experiment_id)
    exp_dir.mkdir(parents=True, exist_ok=True)
    base = _safe(scan_name) + "__" + variant
    target = exp_dir / f"{base}.{ext}"
    # If the same (scan, variant) was exported earlier, disambiguate.
    counter = 2
    while target.exists():
        target = exp_dir / f"{base}_{counter}.{ext}"
        counter += 1
    target.write_bytes(blob)

    return {
        "status": "attached",
        "experiment_id": experiment_id,
        "scan_name": scan_name,
        "filename": target.name,
        "variant": variant,
        "bytes": len(blob),
    }


def _generate_placeholder_image():
    """Generate a placeholder JPEG for simulated mode."""
    if not PIL_AVAILABLE:
        return Response(content=b"", media_type="image/jpeg")

    img = Image.new("RGB", (400, 300), (10, 10, 20))
    draw = ImageDraw.Draw(img)

    # Draw fake Western blot bands
    import random
    random.seed(42)
    for lane in range(6):
        x = 50 + lane * 55
        for band in range(3):
            y = 60 + band * 70 + random.randint(-10, 10)
            w = 30 + random.randint(-5, 5)
            h = 8 + random.randint(-2, 4)
            intensity = random.randint(80, 255)
            # 700 channel = red, 800 channel = green
            if band % 2 == 0:
                color = (intensity, 0, 0)
            else:
                color = (0, intensity, 0)
            draw.ellipse([(x, y), (x + w, y + h)], fill=color)

    draw.text((150, 280), "Simulated", fill=(60, 60, 60))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/jpeg")


# -- Metadata API --

@app.post("/api/record")
async def save_record(data: dict):
    """Save a complete Western blot metadata record."""
    global _current_record
    try:
        record = WesternBlotRecord.from_dict(data)
        # Honor an explicitly-set PID, otherwise stamp the configured one.
        if not record.instrument_pid.strip():
            record.instrument_pid = _identity().get("pid", "")
        record.stamp_timestamp()
        _current_record = record

        safe_name = record.scan_name or "scan"
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in safe_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{safe_name}.json"
        filepath = RECORDS_DIR / filename
        filepath.write_text(record.to_json())

        return {"status": "ok", "filename": filename}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/record/current")
async def get_current_record():
    if _current_record is None:
        return JSONResponse(content=WesternBlotRecord().to_dict())
    return JSONResponse(content=_current_record.to_dict())


@app.get("/api/records")
async def list_records():
    """List recent run records as summaries.

    Returns the metadata needed by the sidebar history panel: identity,
    timestamp, and a condensed subset of scan_settings (resolution,
    quality, intensities, scan area). The history panel renders these
    without having to fetch each full record.
    """
    files = sorted(RECORDS_DIR.glob("*.json"), reverse=True)
    records = []
    for f in files[:50]:
        try:
            d = json.loads(f.read_text())
            s = d.get("scan_settings", {}) or {}
            records.append({
                "filename": f.name,
                "scan_name": d.get("scan_name", ""),
                "operator": d.get("operator", ""),
                "timestamp": d.get("scan_timestamp", ""),
                "project": d.get("project", ""),
                "experiment_id": d.get("experiment_id", ""),
                "scan_group": d.get("scan_group", ""),
                "instrument_pid": d.get("instrument_pid", ""),
                "scan_settings": {
                    "resolution_um": s.get("resolution_um"),
                    "quality": s.get("quality"),
                    "intensity_700": s.get("intensity_700"),
                    "intensity_800": s.get("intensity_800"),
                    "width_cm": s.get("width_cm"),
                    "height_cm": s.get("height_cm"),
                    "focus_offset_mm": s.get("focus_offset_mm"),
                },
                "attachments": _list_attachments(
                    d.get("experiment_id", ""), d.get("scan_name", ""),
                ),
            })
        except Exception:
            continue
    return records


@app.get("/api/experiment/export")
async def export_experiment(experiment_id: str):
    """Bundle every record + image for one experiment (session) into a ZIP.

    Experiments are session-scoped — each page load stamps a fresh
    experiment_id, so this scopes the archive to just the work done
    in that session. Calls the shared exporter with an experiment_id
    filter.
    """
    return await _export_records_matching(
        label="experiment_id",
        value=experiment_id,
        key_in_record="experiment_id",
    )


@app.get("/api/project/export")
async def export_project(project: str):
    """Bundle every record + image for a project (across sessions) into a ZIP.

    Project is a cross-session label. See /api/experiment/export for a
    single-session equivalent. Both delegate to _export_records_matching().
    """
    return await _export_records_matching(
        label="project", value=project, key_in_record="project",
    )


async def _export_records_matching(
    label: str, value: str, key_in_record: str,
):
    """Build a ZIP of all record files whose ``key_in_record`` matches ``value``.

    Shared by the project + experiment export endpoints. Layout::

        <value>/
          README.md                       # summary + scan list
          records/
            <timestamp>_<scan_name>.json  # full metadata per scan
          scans/
            <scan_name>-700.tif           # real hardware
            <scan_name>-800.tif
            <scan_name>-700.png           # simulated fallback
            <scan_name>-800.png
    """
    import zipfile
    if not value or not value.strip():
        raise HTTPException(status_code=400, detail=f"{label} parameter required")
    clean = value.strip()

    matching: list[tuple[Path, dict]] = []
    for f in sorted(RECORDS_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text())
            if (d.get(key_in_record) or "").strip().lower() == clean.lower():
                matching.append((f, d))
        except Exception:
            continue
    if not matching:
        raise HTTPException(
            status_code=404,
            detail=f"No records found where {label}={clean!r}",
        )

    safe = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in clean
    ) or label
    export_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = safe

    def _readme() -> str:
        lines = [
            f"# {clean}",
            "",
            f"Exported {datetime.now().isoformat(timespec='seconds')} from the Odyssey app.",
            f"Contains {len(matching)} scan record(s) where {label} = `{clean}`.",
            "",
            "## Instrument",
            "",
            f"- {_identity().get('name', '(unset)')}",
            f"- PID: <{_identity().get('pid', '(unset)')}>",
            f"- Landing page: <{_identity().get('landing_page', '(unset)')}>",
            "",
            "## Scans (newest first)",
            "",
        ]
        for _, d in matching:
            s = d.get("scan_settings", {}) or {}
            settings = [
                f"{s.get('resolution_um')} µm" if s.get("resolution_um") is not None else None,
                s.get("quality"),
                f"700:{s.get('intensity_700')} / 800:{s.get('intensity_800')}"
                if s.get("intensity_700") is not None else None,
                f"{s.get('width_cm')}×{s.get('height_cm')} cm"
                if s.get("width_cm") is not None else None,
            ]
            settings_str = " · ".join(str(x) for x in settings if x is not None)
            lines.append(
                f"- **{d.get('scan_name', '(unnamed)')}** — "
                f"{d.get('operator') or 'no operator'} · "
                f"{d.get('scan_timestamp') or '—'}"
                + (f"  \n  {settings_str}" if settings_str else "")
            )
        lines += ["", "## Layout", "",
                  "- `records/` — one JSON file per scan with the full metadata.",
                  "- `scans/`   — instrument TIFFs when available (real hardware)",
                  "               or rendered PNG previews (simulated mode).",
                  ""]
        return "\n".join(lines)

    identity = _identity()
    manifest = {
        "instrument_pid": identity.get("pid", ""),
        "instrument_landing_page": identity.get("landing_page", ""),
        "instrument_name": identity.get("name", ""),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "value": clean,
        "scan_count": len(matching),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{root}/README.md", _readme())
        z.writestr(f"{root}/manifest.json", json.dumps(manifest, indent=2))

        for f, d in matching:
            z.writestr(f"{root}/records/{f.name}", f.read_text())

            scan_name = d.get("scan_name") or "unnamed"
            group = d.get("scan_group") or DEFAULT_GROUP
            safe_scan = "".join(
                c if c.isalnum() or c in "-_" else "_" for c in scan_name
            )

            tiff_wrote = False
            if (
                _image_driver and _odyssey_driver
                and hasattr(_image_driver.backend, "download_channel")
            ):
                for ch in (700, 800):
                    try:
                        data = await _image_driver.backend.download_channel(
                            group, scan_name, ch,
                        )
                        if data:
                            data = _tag_tiff(
                                data, scan_name=scan_name, channel=ch,
                            )
                            z.writestr(f"{root}/scans/{safe_scan}-{ch}.tif", data)
                            tiff_wrote = True
                    except Exception as e:
                        logging.info(
                            "Export: could not fetch %s/%s-%d: %s",
                            group, scan_name, ch, e,
                        )

            if not tiff_wrote:
                for ch in (700, 800):
                    try:
                        png = await _render_single_channel(group, scan_name, ch)
                        if png:
                            z.writestr(f"{root}/scans/{safe_scan}-{ch}.png", png)
                    except Exception as e:
                        logging.info(
                            "Export: render fallback failed for %s-%d: %s",
                            scan_name, ch, e,
                        )

            # Bundle any per-scan attachments (PNG/TIFF exports the user
            # has attached via /api/image/export).
            exp_id_for_scan = d.get("experiment_id", "")
            exp_dir = EXPORTS_DIR / _safe(exp_id_for_scan) if exp_id_for_scan else None
            if exp_dir and exp_dir.exists():
                prefix = _safe(scan_name) + "__"
                for att in sorted(exp_dir.glob(f"{prefix}*")):
                    z.writestr(
                        f"{root}/exports/{att.name}",
                        att.read_bytes(),
                    )

    buf.seek(0)
    filename = f"{safe}_{export_ts}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/diagnostics/probe")
async def diagnostics_probe():
    """Hit every candidate scan-related path and report what came back.

    The Odyssey embedded server can mount the scan UI at different paths
    depending on firmware (nonjava vs java) and version. When configure
    is 404-ing, this endpoint tells us which paths the instrument
    actually serves so we don't have to guess.

    Probes are GETs (safe — no state change). For each path we record
    HTTP status, Content-Type, response length, and the first 600 bytes
    of the body. The scan-landing GETs include the full HTML so the
    `<form action="...">` attribute is visible.
    """
    if _odyssey_driver is None:
        return {"mode": "simulated", "note": "no instrument in this mode"}

    candidates = [
        # Most likely paths first — landing pages reveal form actions.
        "/scanapp/scan/nonjava/",
        "/scanapp/scan/java/",
        "/scanapp/scan/",
        "/scanapp/",
        "/",
        # Direct CGI endpoints — these tell us if config.pl exists at all.
        "/scanapp/scan/nonjava/config.pl",
        "/scanapp/scan/nonjava/configure.pl",
        "/scanapp/scan/java/config.pl",
        "/scanapp/scan/configure.pl",
        # Status / utility endpoints — confirms reachability + session.
        "/scanapp/util/status/",
        "/scanapp/imaging/nonjava/info.pl",
    ]

    results = []
    session = _odyssey_driver._check_session()
    base = _odyssey_driver.base_url
    for path in candidates:
        url = f"{base}{path}"
        try:
            async with session.get(url, allow_redirects=False) as resp:
                body = await resp.text()
                results.append({
                    "path": path,
                    "http_status": resp.status,
                    "content_type": resp.headers.get("Content-Type", ""),
                    "location": resp.headers.get("Location", ""),
                    "set_cookie": resp.headers.get("Set-Cookie", ""),
                    "length": len(body),
                    "body_head": body[:600],
                })
        except Exception as e:
            results.append({
                "path": path,
                "error": f"{type(e).__name__}: {e}",
            })
    return {"mode": "real", "base_url": base, "probes": results}


@app.get("/api/diagnostics/configure")
async def diagnostics_configure():
    """Return the last configure_scan() POST attempt — URL hit, HTTP
    status returned, and the response body — alongside a fresh GET of
    the scan landing page so the form's actual ``action=`` URL is
    visible. Use when configure is silently failing or returning a 4xx.
    """
    if _odyssey_driver is None:
        return {"mode": "simulated", "note": "no instrument in this mode"}
    out = {
        "mode": "real",
        "last_configure": {
            "url": getattr(_odyssey_driver, "_last_configure_url", ""),
            "http_status": getattr(_odyssey_driver, "_last_configure_http", 0),
            "body": getattr(_odyssey_driver, "_last_configure_body", ""),
        },
    }
    # GET the scan landing page so we can see what URL its <form>
    # element actually posts to. This is the authoritative answer to
    # "what URL does configure go to" on this firmware.
    try:
        landing = await _odyssey_driver.get("/scanapp/scan/nonjava/")
        out["scan_landing_html"] = landing
    except Exception as e:
        out["scan_landing_error"] = f"{type(e).__name__}: {e}"
    return out


@app.get("/api/diagnostics/status")
async def diagnostics_status():
    """Return the raw status HTML from the instrument plus the parsed
    fields, side by side. Open this in a browser when the parser is
    misbehaving — no copy-paste from the terminal needed.

    On real hardware: triggers a fresh status fetch, then surfaces
    the cached raw HTML and HTTP status from the driver.
    On simulated mode: returns a small synthetic record so the
    endpoint shape is stable for the UI to consume.
    """
    if _odyssey_driver is None:
        return {
            "mode": "simulated",
            "http_status": None,
            "raw_html": "(simulated mode — no instrument page)",
            "parsed": {
                "state": "Idle", "current_user": "", "progress": "0",
                "time_remaining": "", "lid_status": "closed",
            },
        }
    try:
        parsed = await _odyssey_driver.get_status()
    except Exception as e:
        return {
            "mode": "real",
            "error": f"{type(e).__name__}: {e}",
            "http_status": getattr(_odyssey_driver, "_last_status_http", 0),
            "raw_html": getattr(_odyssey_driver, "_last_status_html", ""),
            "parsed": None,
        }
    return {
        "mode": "real",
        "http_status": _odyssey_driver._last_status_http,
        "raw_html": _odyssey_driver._last_status_html,
        "parsed": parsed,
    }


@app.post("/api/scan/reset")
async def reset_instrument():
    """Force the instrument back to Idle. Sends Cancel via the scan
    capability, then a status-page force_stop, then re-reads state.

    Each step is independently try/excepted so that a failure of one
    doesn't prevent the next: cancel may 404 if the scanner thinks no
    scan is in flight, but the status-page stop will still pull motor
    movement / partial scans down. Returns the final observed state so
    the UI can update its status display.
    """
    if _scan_driver is None:
        return {"status": "no-op", "note": "no instrument in this mode"}

    errors = []
    try:
        await _scan_driver.cancel()
    except Exception as e:
        errors.append(f"cancel: {type(e).__name__}: {e}")

    if _status_driver is not None and hasattr(_status_driver.backend, "force_stop"):
        try:
            await _status_driver.backend.force_stop()
        except Exception as e:
            errors.append(f"force_stop: {type(e).__name__}: {e}")

    final_state = "Unknown"
    try:
        if _status_driver is not None:
            reading = await _status_driver.read()
            final_state = reading.state
    except Exception as e:
        errors.append(f"read_status: {type(e).__name__}: {e}")

    _scan_state["state"] = final_state
    _scan_state["progress"] = 0
    _scan_state["time_remaining"] = ""
    await _broadcast({"type": "status", **_scan_state})

    return {"status": "ok", "state": final_state, "warnings": errors}


@app.post("/api/quit")
async def quit_app():
    """Graceful server shutdown initiated from the Quit button.

    Refuses while a scan is actively in flight (Initializing / Scanning
    / Paused). On approval: closes the driver, returns 200, then exits
    the process from a slightly-delayed callback so the response has
    time to flush to the client.
    """
    state = (_scan_state.get("state") or "Idle")
    busy = {"Initializing", "Scanning", "Paused"}
    if state in busy:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot quit while a scan is in progress (state={state}).",
        )
    if _odyssey_driver is not None:
        try:
            await _odyssey_driver.stop()
        except Exception as e:
            logging.warning("Error stopping driver during quit: %s", e)
    # Defer the actual exit so this 200 response makes it back to the
    # browser. os._exit skips the FastAPI shutdown lifespan, but the
    # driver is already stopped above, so there's nothing left to clean.
    asyncio.get_event_loop().call_later(0.3, lambda: os._exit(0))
    return {"status": "stopping"}


@app.get("/api/connection")
async def connection_info():
    """Report connection mode (real vs simulated) plus instrument identity."""
    identity = _identity()
    base = {
        "instrument_pid": identity.get("pid", ""),
        "instrument_landing_page": identity.get("landing_page", ""),
        "instrument_name": identity.get("name", ""),
    }
    if _odyssey_driver:
        return {**base, "mode": "real", "host": _odyssey_driver.base_url}
    return {**base, "mode": "simulated"}
