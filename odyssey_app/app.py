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

# Conditionally import PLR + image libraries
try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from plr_v4.odyssey.connection import (
        OdysseyConnection, ScanParameters, DEFAULT_GROUP,
    )
    from plr_v4.odyssey.status_backend import normalize_state, InstrumentState
    from plr_v4.capabilities.scanning import Scanning
    from plr_v4.capabilities.image_retrieval import ImageRetrieval
    from plr_v4.capabilities.instrument_status import InstrumentStatusCapability
    from plr_v4.odyssey.simulated import (
        OdysseyState,
        OdysseyScanSimulated,
        OdysseyImageSimulated,
        OdysseyStatusSimulated,
    )
    PLR_AVAILABLE = True
except ImportError:
    PLR_AVAILABLE = False
    DEFAULT_GROUP = "odyssey"
    def normalize_state(raw: str) -> str:
        return raw or "Idle"

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
RECORDS_DIR.mkdir(exist_ok=True)
SCANS_DIR.mkdir(exist_ok=True)

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
_odyssey_connection = None
_sim_state = None
_scan_driver = None
_image_driver = None
_status_driver = None


@app.on_event("startup")
async def startup():
    """Initialize PLR driver — real or simulated.

    FR-027: credentials come from ODYSSEY_USER/ODYSSEY_PASS env vars via
    OdysseyConnection.from_env(). If ODYSSEY_HOST is set but credentials
    are missing, we fail loudly with a log message and fall back to
    simulated mode rather than silently connecting with defaults.
    """
    global _odyssey_connection, _sim_state
    global _scan_driver, _image_driver, _status_driver

    if not PLR_AVAILABLE:
        return

    host = os.environ.get("ODYSSEY_HOST", "")
    if host:
        # Real instrument connection (FR-027)
        try:
            _odyssey_connection = OdysseyConnection.from_env(host=host)
        except ValueError as e:
            logging.warning(
                "Real-hardware mode requested (ODYSSEY_HOST=%s) but "
                "credentials are missing: %s. Falling back to simulated mode.",
                host, e,
            )
            _setup_simulated()
            return
        try:
            await _odyssey_connection.setup()
            from plr_v4.odyssey.scan_backend import OdysseyScanBackend
            from plr_v4.odyssey.image_backend import OdysseyImageBackend
            from plr_v4.odyssey.status_backend import OdysseyStatusBackend
            _scan_driver = Scanning(backend=OdysseyScanBackend(_odyssey_connection))
            _image_driver = ImageRetrieval(backend=OdysseyImageBackend(_odyssey_connection))
            _status_driver = InstrumentStatusCapability(
                backend=OdysseyStatusBackend(_odyssey_connection)
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
            _odyssey_connection = None
            _setup_simulated()
    else:
        _setup_simulated()


def _setup_simulated():
    """Set up simulated PLR backends wrapped in capability frontends."""
    global _sim_state, _scan_driver, _image_driver, _status_driver
    _sim_state = OdysseyState()
    _scan_driver = Scanning(backend=OdysseyScanSimulated(_sim_state))
    _image_driver = ImageRetrieval(backend=OdysseyImageSimulated(_sim_state))
    _status_driver = InstrumentStatusCapability(
        backend=OdysseyStatusSimulated(_sim_state)
    )
    _scan_driver._setup_finished = True
    _image_driver._setup_finished = True
    _status_driver._setup_finished = True


@app.on_event("shutdown")
async def shutdown():
    """Close PLR connection."""
    if _odyssey_connection is not None:
        await _odyssey_connection.stop()


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

@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text()


# -- Scan API --

@app.post("/api/scan/configure")
async def configure_scan(data: dict):
    """Configure a scan with parameters from the GUI.

    FR-018: scan_group must be 'odyssey'. This is enforced server-side
    so that direct API clients (curl, tests, dev-tools) cannot bypass
    the GUI's locked-group field.
    """
    global _scan_state
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


_TERMINAL_STATES = {"Completed", "Stopped", "Failed"}


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
        elif _status_driver and _odyssey_connection:
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
    """Background task: poll Odyssey status until scan reaches a terminal state.

    Emits exactly one scan_complete broadcast on the first transition into
    Completed, Stopped, or Failed (FR-001, FR-002, plan D9). Unknown states
    are logged and mapped to 'Failed' by normalize_state in the status
    backend, so this loop only ever sees canonical InstrumentState values.
    """
    global _scan_state
    logging.info("Starting scan progress polling...")
    emitted = False
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

            if state in _TERMINAL_STATES and not emitted:
                if state == "Completed":
                    _scan_state["progress"] = 100
                    await _broadcast({"type": "status", **_scan_state})
                await _emit_scan_complete(state)
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
    if _status_driver and _odyssey_connection:
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
    if _scan_driver and _odyssey_connection and hasattr(_scan_driver.backend, "estimate_time"):
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
    if _image_driver and _odyssey_connection and hasattr(
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
    if _image_driver and _odyssey_connection and hasattr(_image_driver.backend, "get_preview"):
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
    if _image_driver and _odyssey_connection and hasattr(_image_driver.backend, "download_channel"):
        try:
            tiff_bytes = await _image_driver.backend.download_channel(group, scan, channel)
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
    """Export the image with display settings and optional metadata footer.

    Accepts a FLAT payload (FR-017) — metadata fields live at the top level,
    not nested under a 'metadata' key. Any 'metadata' key that a legacy
    client sends is ignored defensively. The 'view' field selects 700 /
    800 / overlay per the current viewer state.
    """
    if not PIL_AVAILABLE:
        raise HTTPException(status_code=500, detail="Pillow not installed")

    scan_name = data.get("scan_name", "export")
    include_footer = data.get("include_footer", True)
    format = data.get("format", "png")  # "png" or "tiff"
    view = data.get("view", "700")  # "700" | "800" | "overlay"
    # Read metadata fields from the FLAT payload. Accept a nested
    # "metadata" dict too as a legacy escape hatch, but prefer top-level.
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else data

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
        img.save(buf, format="TIFF")
        media_type = "image/tiff"
        ext = "tif"
    else:
        img.save(buf, format="PNG")
        media_type = "image/png"
        ext = "png"

    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={scan_name}.{ext}"},
    )


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
                "scan_group": d.get("scan_group", ""),
                "scan_settings": {
                    "resolution_um": s.get("resolution_um"),
                    "quality": s.get("quality"),
                    "intensity_700": s.get("intensity_700"),
                    "intensity_800": s.get("intensity_800"),
                    "width_cm": s.get("width_cm"),
                    "height_cm": s.get("height_cm"),
                    "focus_offset_mm": s.get("focus_offset_mm"),
                },
            })
        except Exception:
            continue
    return records


@app.get("/api/connection")
async def connection_info():
    """Report connection mode (real vs simulated)."""
    if _odyssey_connection:
        return {
            "mode": "real",
            "host": _odyssey_connection.base_url,
        }
    return {"mode": "simulated"}
