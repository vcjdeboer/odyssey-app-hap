"""Notebook / script quickstart for the Odyssey driver.

This file is designed to be pasted cell-by-cell into a Jupyter notebook,
or run directly as a script:

    # Simulated (no hardware):
    python -m plr_v4.odyssey.notebook_quickstart

    # Real instrument:
    ODYSSEY_HOST=169.254.206.190 \\
      ODYSSEY_USER=<user> ODYSSEY_PASS=<pass> \\
      python -m plr_v4.odyssey.notebook_quickstart --real

The sections are marked with "# %%" so Jupyter's "Import Python file"
extension and VS Code's Python Interactive understand them as cells.
"""

# %% --------------------------------------------------------------------
# Cell 1 — imports
import asyncio
import os

from plr_v4.odyssey import OdysseyClassic, ScanParameters, DEFAULT_GROUP


# %% --------------------------------------------------------------------
# Cell 2 — simulated scan (no hardware)
#
# OdysseyClassic.simulated() returns an in-memory device whose behaviour
# matches the real driver (state machine, terminal states, partial-image
# on Stop). Good for notebooks, CI, and dev machines.

async def run_simulated_scan() -> bytes:
    async with OdysseyClassic.simulated() as odyssey:
        params = ScanParameters(
            name="demo_run",
            group=DEFAULT_GROUP,        # "odyssey" — FR-026
            resolution="169",           # µm — strings to match the CGI form
            quality="medium",
            intensity_700="5",
            intensity_800="5",
            width=10,                   # cm
            height=10,
        )
        # scan() = configure → start → poll until terminal. Returns the
        # final InstrumentStatusReading so you can inspect it.
        status = await odyssey.scan(
            params,
            poll_interval=0.1,
            on_progress=lambda s: print(f"  {s.state:<11s} progress={s.progress:5.1f}%"),
        )
        print(f"→ final state: {status.state}")

        # Download the TIFF from the (simulated) instrument storage.
        tiff = await odyssey.images.download(DEFAULT_GROUP, params.name)
        print(f"→ downloaded {len(tiff)} bytes")
        return tiff


# %% --------------------------------------------------------------------
# Cell 3 — real-hardware scan
#
# Same API, different factory. from_env() reads ODYSSEY_USER and
# ODYSSEY_PASS from the environment (FR-027). Credentials never live in
# code or config files. If ODYSSEY_HOST is set, you can omit ``host``.

async def run_real_scan(host: str, scan_name: str) -> bytes:
    async with OdysseyClassic.from_env(host=host) as odyssey:
        params = ScanParameters(
            name=scan_name,
            group=DEFAULT_GROUP,
            resolution="169",
            quality="medium",
            intensity_700="5",
            intensity_800="5",
            width=10,
            height=10,
        )
        status = await odyssey.scan(
            params,
            poll_interval=2.0,
            on_progress=lambda s: print(f"  {s.state:<11s} progress={s.progress:5.1f}%"),
        )
        if status.state != "Completed":
            raise RuntimeError(f"Scan did not complete cleanly: {status}")

        ch700 = await odyssey.images.backend.download_channel(
            DEFAULT_GROUP, scan_name, 700
        )
        ch800 = await odyssey.images.backend.download_channel(
            DEFAULT_GROUP, scan_name, 800
        )
        print(f"→ 700 nm: {len(ch700)} bytes; 800 nm: {len(ch800)} bytes")
        return ch700 + ch800


# %% --------------------------------------------------------------------
# Cell 4 — low-level access to each capability
#
# If you need more control, the three capabilities are directly available.

async def demo_low_level() -> None:
    async with OdysseyClassic.simulated() as odyssey:
        # Check status before configuring.
        status = await odyssey.status.read()
        print(f"state before scan: {status.state}")

        # Manual configure / start / wait / download.
        await odyssey.scanning.configure(ScanParameters(name="lowlevel"))
        await odyssey.scanning.start()
        final = await odyssey.wait_until_done(poll_interval=0.05)
        print(f"state after scan: {final.state}")

        # List what's in the group.
        groups = await odyssey.images.list_groups()
        scans = await odyssey.images.list_scans(DEFAULT_GROUP)
        print(f"groups: {groups}")
        print(f"scans in odyssey: {scans}")


# %% --------------------------------------------------------------------
# Cell 5 — driver in script mode

def main() -> None:
    import sys
    if "--real" in sys.argv:
        host = os.environ.get("ODYSSEY_HOST", "169.254.206.190")
        asyncio.run(run_real_scan(host, scan_name="notebook_demo"))
    else:
        asyncio.run(run_simulated_scan())
        print()
        asyncio.run(demo_low_level())


if __name__ == "__main__":
    main()
