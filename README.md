# odyssey-app-hap

FastAPI control app for the **LI-COR Odyssey Classic** (model 9120) infrared imaging system at the **Human and Animal Physiology** lab, Wageningen University & Research.

This is the first real-world deployment of the [pylabrobot Odyssey driver and DeviceCard provenance pattern](https://github.com/vcjdeboer/pylabrobot/tree/odyssey-v1b1). The app provides scan setup, live preview, recording, and FAIR-style provenance metadata embedded in every scan output.

## Why this exists

LI-COR's Image Studio software didn't run on Windows 11 in our hands. The Odyssey ships an embedded Linux web server (Apache/mod_perl) with a CGI API that drives every operation. This app talks to that API directly — no vendor software required.

## Provenance / PIDInst

The instrument is registered with a persistent identifier on [b2inst](https://b2inst.gwdg.de):

> **PIDInst Handle**: [`hdl.handle.net/21.11157/psf97-zv353`](http://hdl.handle.net/21.11157/psf97-zv353)
>
> **Landing page**: https://b2inst.gwdg.de/records/psf97-zv353

That handle is embedded in every TIFF (tag 270 ImageDescription) and PNG (tEXt chunk) the app produces. A scan separated from its surrounding context still resolves back to the instrument that produced it.

## Architecture

```
┌────────────────────────────────────────────────┐
│ Browser UI (odyssey_app/static/index.html)     │
│   ↕ WebSocket + REST                           │
│ FastAPI app (odyssey_app/app.py)               │
│   ↓                                            │
│ DeviceCard with PIDInst identity (instrument.py)│
│   ↓                                            │
│ plr_v4 Odyssey driver (vendored, transitional) │
│   ↓                                            │
│ Odyssey Classic firmware 2.1.12                │
│ (Apache 1.3.27 / mod_perl on the instrument)   │
└────────────────────────────────────────────────┘
```

`odyssey_app/instrument.py` constructs the lab's instance card (PID, landing page, friendly name) and merges it onto the model-base card. The merged card travels into the metadata of every TIFF/PNG/JSON the app emits.

### About the vendored `plr_v4/`

The Odyssey driver started as a prototype in [`vcjdeboer/titronic`](https://github.com/vcjdeboer/titronic) under the `plr_v4` package. It's now upstream-bound at [PR vcjdeboer/pylabrobot:odyssey-v1b1](https://github.com/vcjdeboer/pylabrobot/tree/odyssey-v1b1) as `pylabrobot.li_cor.odyssey`. **Until that PR merges**, this repo vendors the Odyssey-relevant slice of the prototype (`plr_v4/odyssey/`, `plr_v4/capabilities/`, plus three small base modules) so the app installs and runs standalone. Vendored size is ~150 KB. When the upstream PR lands, a single migration commit will:
- Delete `plr_v4/`
- Switch `odyssey_app/` imports to `pylabrobot.li_cor.odyssey`

## Running locally

```bash
pip install -e .
export ODYSSEY_HOST=169.254.206.190    # link-local from a direct cable to the instrument
export ODYSSEY_USER=...
export ODYSSEY_PASS=...
uvicorn odyssey_app.app:app --port 8000
# open http://localhost:8000
```

Without `ODYSSEY_HOST`, the app falls back to a simulated path (no instrument required) — useful for UI development.

## Deploying to a Windows lab box

`scripts/deploy_to_desktop.sh` builds a one-shot bundle:
- A snapshot of the app + dependencies
- Two Desktop shortcuts: **Odyssey Server** (starts uvicorn) and **Odyssey App** (opens the browser)
- A `credentials.bat.example` template — the lab user fills credentials once, never committed

Run from a Mac / Linux machine that has the lab Windows box mounted as a shared folder, then double-click the shortcut creator on the Windows box.

## Status

- **Lab box (HAP005)**: running an earlier prototype checkout. This repo is the future home; the lab will roll over on the next deploy.
- **pylabrobot upstream PR**: in review at [vcjdeboer/pylabrobot:odyssey-v1b1](https://github.com/vcjdeboer/pylabrobot/tree/odyssey-v1b1). When it merges, this repo migrates to the upstream import path.
- **PIDInst record**: published at b2inst as `hdl.handle.net/21.11157/psf97-zv353`. Every scan written by this app carries that handle in TIFF tag 270 and PNG tEXt.

## Layout

```
odyssey-app-hap/
├── odyssey_app/
│   ├── app.py            # FastAPI app (REST + WebSocket)
│   ├── instrument.py     # this lab's DeviceCard instance (PIDInst identity)
│   ├── metadata.py       # WesternBlotRecord schema
│   ├── static/           # browser UI
│   ├── records/          # saved metadata (gitignored)
│   ├── exports/          # ZIP exports per experiment (gitignored)
│   └── scans/            # cached scan files (gitignored)
├── plr_v4/               # vendored prototype slice (transitional, see above)
│   ├── odyssey/
│   ├── capabilities/
│   ├── device.py
│   ├── device_card.py
│   └── signals.py
├── scripts/
│   ├── deploy_to_desktop.sh
│   └── odyssey.ico
├── pyproject.toml
├── README.md
└── LICENSE
```

## License

[MIT](LICENSE) — same as PyLabRobot upstream.
