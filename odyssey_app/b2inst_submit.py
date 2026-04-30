"""Submit a b2inst draft for community review (last step before publication).

Usage:
    export B2INST_TOKEN='...'
    python3 odyssey_app/b2inst_submit.py psf97-zv353
    python3 odyssey_app/b2inst_submit.py psf97-zv353 --community eudat

Two API calls:
  PUT  /api/records/<id>/draft/review              ← link draft to community
  POST /api/records/<id>/draft/actions/submit-review  ← put it in the queue

After this, the draft enters the community's curator queue. A curator
accepts → record is published with a permanent Handle PID. Until they
accept, you can still cancel from the UI.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request


HOST = os.environ.get("B2INST_HOST", "https://b2inst.pid.gwdg.de").rstrip("/")
TOKEN = os.environ.get("B2INST_TOKEN")
VERIFY_TLS = os.environ.get("B2INST_VERIFY_TLS", "1") == "1"

if not TOKEN:
    sys.exit("ERROR: B2INST_TOKEN is not set in the environment.")


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
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"{method} {path} -> HTTP {e.code} {e.reason}")
        try:
            print(json.dumps(json.loads(err_body), indent=2))
        except Exception:
            print(err_body)
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("record_id")
    ap.add_argument("--community", default="eudat",
                    help="Community slug (default: eudat)")
    args = ap.parse_args()

    rid = args.record_id
    print(f"Submitting draft {rid} for review under community '{args.community}'\n")

    print("Looking up community id by slug:")
    c = request("GET", f"/api/communities/{args.community}")
    cid = c["id"]
    print(f"  '{args.community}' -> {cid}\n")

    print("Step 5/6: link the draft to the community as a review request")
    request(
        "PUT",
        f"/api/records/{rid}/draft/review",
        {"receiver": {"community": cid}, "type": "community-submission"},
    )

    print()
    print("Step 6/6: submit the review request")
    result = request("POST", f"/api/records/{rid}/draft/actions/submit-review")

    print()
    print("Submitted.")
    print(f"  Request id:     {result.get('id')}")
    print(f"  Request status: {result.get('status')}")
    print()
    print("What happens next:")
    print(f"  - track at:  {HOST}/me/requests")
    print(f"  - draft UI:  https://b2inst.gwdg.de/uploads/{rid}")
    print("  - a curator at the community accepts/declines")
    print("  - on accept, the record is published with a permanent Handle PID")


if __name__ == "__main__":
    main()
