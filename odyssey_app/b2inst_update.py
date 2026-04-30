"""Update an existing b2inst draft record with the current Odyssey METADATA.

Usage:
    export B2INST_TOKEN='...'
    python3 odyssey_app/b2inst_update.py psf97-zv353

Sends PUT /api/records/<id>/draft with the same METADATA defined in
b2inst_register.py. Drafts are mutable until published, so this is the
mechanism for fixing typos, replacing TBD-LICOR-SN with the real serial,
adjusting the description, etc.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

from b2inst_register import METADATA  # reuse the single source of truth


HOST = os.environ.get("B2INST_HOST", "https://b2inst.pid.gwdg.de").rstrip("/")
TOKEN = os.environ.get("B2INST_TOKEN")
VERIFY_TLS = os.environ.get("B2INST_VERIFY_TLS", "1") == "1"

if not TOKEN:
    sys.exit("ERROR: B2INST_TOKEN is not set in the environment.")

if len(sys.argv) != 2:
    sys.exit("Usage: python3 b2inst_update.py <record-id>   (e.g. psf97-zv353)")

RID = sys.argv[1]


def request(method: str, path: str, body: dict | None = None) -> dict:
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
    ctx = ssl.create_default_context()
    if not VERIFY_TLS:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            print(f"{method} {path} -> HTTP {resp.status}")
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"{method} {path} -> HTTP {e.code} {e.reason}")
        try:
            print(json.dumps(json.loads(err_body), indent=2))
        except Exception:
            print(err_body)
        sys.exit(1)


def main() -> None:
    print(f"Updating draft {RID} on {HOST}\n")

    # Show what's currently stored, for the diff feeling.
    before = request("GET", f"/api/records/{RID}/draft")
    print("--- BEFORE: server-stored AlternateIdentifier ---")
    print(json.dumps(before.get("metadata", {}).get("AlternateIdentifier"), indent=2))
    print()
    print("--- BEFORE: server-stored RelatedIdentifier ---")
    print(json.dumps(before.get("metadata", {}).get("RelatedIdentifier"), indent=2))
    print()

    # PUT the corrected metadata. We send the FULL metadata, not a partial
    # patch — PUT replaces the metadata document.
    after = request("PUT", f"/api/records/{RID}/draft", {"metadata": METADATA})
    print()
    print("--- AFTER: server-stored AlternateIdentifier ---")
    print(json.dumps(after.get("metadata", {}).get("AlternateIdentifier"), indent=2))
    print()
    print("--- AFTER: server-stored RelatedIdentifier ---")
    print(json.dumps(after.get("metadata", {}).get("RelatedIdentifier"), indent=2))
    print()
    print(f"Done. Reload the UI to see the changes:")
    print(f"  https://b2inst.gwdg.de/uploads/{RID}")


if __name__ == "__main__":
    main()
