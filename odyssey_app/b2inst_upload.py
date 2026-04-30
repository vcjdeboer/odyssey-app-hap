"""Attach files (e.g. a photo of the device) to a b2inst draft record.

Usage:
    export B2INST_TOKEN='...'
    python3 odyssey_app/b2inst_upload.py psf97-zv353 odyssey-front.jpg
    python3 odyssey_app/b2inst_upload.py psf97-zv353 photo1.jpg backplate.jpg

Three API calls per file:
  POST /api/records/<id>/draft/files                ← register filenames
  PUT  /api/records/<id>/draft/files/<name>/content ← upload bytes
  POST /api/records/<id>/draft/files/<name>/commit  ← finalize

Note: files are FROZEN once you publish. To add or replace later, cut a
new version (POST /api/records/<id>/versions).
"""
from __future__ import annotations

import json
import mimetypes
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path


HOST = os.environ.get("B2INST_HOST", "https://b2inst.pid.gwdg.de").rstrip("/")
TOKEN = os.environ.get("B2INST_TOKEN")
VERIFY_TLS = os.environ.get("B2INST_VERIFY_TLS", "1") == "1"

if not TOKEN:
    sys.exit("ERROR: B2INST_TOKEN is not set in the environment.")


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not VERIFY_TLS:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def request_json(method: str, path: str, body: dict | list | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{HOST}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, context=_ctx()) as resp:
            print(f"{method} {path} -> HTTP {resp.status}")
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8")
        print(f"{method} {path} -> HTTP {e.code} {e.reason}")
        try:
            print(json.dumps(json.loads(err), indent=2))
        except Exception:
            print(err)
        sys.exit(1)


def put_bytes(path: str, data: bytes, content_type: str) -> None:
    req = urllib.request.Request(
        f"{HOST}{path}",
        data=data,
        method="PUT",
        headers={
            "Content-Type": content_type,
            "Authorization": f"Bearer {TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, context=_ctx()) as resp:
            print(f"PUT {path}  ({len(data):,} bytes)  -> HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8")
        print(f"PUT {path} -> HTTP {e.code} {e.reason}\n{err}")
        sys.exit(1)


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("Usage: python3 b2inst_upload.py <record-id> <file> [<file>...]")
    rid = sys.argv[1]
    paths = [Path(p) for p in sys.argv[2:]]
    for p in paths:
        if not p.is_file():
            sys.exit(f"Not a file: {p}")

    keys = [{"key": p.name} for p in paths]
    print(f"Registering {len(paths)} file(s) on draft {rid}:")
    for k in keys:
        print(f"  - {k['key']}")
    print()
    request_json("POST", f"/api/records/{rid}/draft/files", keys)

    for p in paths:
        ctype, _ = mimetypes.guess_type(p.name)
        ctype = ctype or "application/octet-stream"
        data = p.read_bytes()
        print()
        print(f"Uploading {p.name}  ({len(data):,} bytes, {ctype})")
        put_bytes(f"/api/records/{rid}/draft/files/{p.name}/content", data, ctype)
        request_json("POST", f"/api/records/{rid}/draft/files/{p.name}/commit")

    print()
    print(f"Done. Reload UI:  https://b2inst.gwdg.de/uploads/{rid}")


if __name__ == "__main__":
    main()
