"""Register the Odyssey on b2inst as a PIDINST draft record.

Usage:
    export B2INST_TOKEN='...'           # required
    export B2INST_HOST='https://141.5.103.19'   # default; demo host
    python b2inst_register.py

This script does ONE thing: POST /api/records with the Odyssey payload to
create a DRAFT. It does NOT publish, attach files, or submit to a community.
Inspect the draft after, then we'll do the next steps separately.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request


HOST = os.environ.get("B2INST_HOST", "https://b2inst.pid.gwdg.de").rstrip("/")
TOKEN = os.environ.get("B2INST_TOKEN")
# Production has a real cert (verify=on). For the demo IP host
# (https://141.5.103.19) set B2INST_VERIFY_TLS=0.
VERIFY_TLS = os.environ.get("B2INST_VERIFY_TLS", "1") == "1"

if not TOKEN:
    sys.exit("ERROR: B2INST_TOKEN is not set in the environment.")


# PIDINST payload for the Odyssey. Mandatory fields per PIDINST that b2inst
# auto-fills on publish (Identifier, SchemaVersion, LandingPage) are omitted.
METADATA: dict = {
    "Name": "LI-COR Odyssey Classic 9120 — Human and Animal Physiology, WUR",
    # Where the Handle PID resolves to. b2inst's own record URL works as a
    # self-referential landing page; replace with a lab-maintained instrument
    # page if/when one exists.
    "LandingPage": "https://b2inst.gwdg.de/records/psf97-zv353",
    "Owner": [
        {
            "ownerName": "Human and Animal Physiology",
            "ownerContact": "vincent.deboer@wur.nl",
            "ownerIdentifier": {
                "ownerIdentifierType": "Other",
                "ownerIdentifierValue": (
                    "https://www.wur.nl/en/research-results/chair-groups/"
                    "animal-sciences/human-and-animal-physiology.htm"
                ),
            },
        },
        {
            "ownerName": "Wageningen University & Research",
            "ownerIdentifier": {
                "ownerIdentifierType": "Other",
                "ownerIdentifierValue": "https://ror.org/04qw24q55",
            },
        },
    ],
    "Manufacturer": [
        {
            "manufacturerName": "LI-COR Biosciences",
            "manufacturerIdentifier": {
                "manufacturerIdentifierType": "URL",
                "manufacturerIdentifierValue": "https://www.licor.com",
            },
        }
    ],
    "Model": {"modelName": "Odyssey Classic 9120"},
    "Description": (
        "Two-channel near-infrared (NIR) fluorescence imaging system designed "
        "for quantitative Western blot detection. The Odyssey Classic 9120 "
        "simultaneously scans at 700 nm and 800 nm, producing two-color, "
        "low-background images of NIR-dye–labeled antibodies on nitrocellulose "
        "or PVDF membranes with linear dynamic range. Typical applications "
        "include quantitative Western blotting, in-cell Western assays, and "
        "small-molecule fluorescence imaging.\n\n"
        "Manufactured by LI-COR in August 2007, commissioned at WUR in 2008 "
        "(asset tag ASGHAP20080256). The instrument is operated at the Human "
        "and Animal Physiology chair group at Wageningen University & "
        "Research. It is integrated into a PyLabRobot-based control stack via "
        "an in-house 'odyssey_app' that wraps the LI-COR HTTP control "
        "interface, captures structured experiment metadata (samples, "
        "antibodies, scan settings) per scan, and stamps each TIFF with this "
        "PIDINST handle for downstream traceability."
    ),
    # b2inst's UI marks the instrument-type identifier fields as required even
    # though many published records leave them blank. We satisfy the form by
    # pointing each entry at the LI-COR product page (loose but defensible —
    # there is no canonical vocabulary for niche bench instruments).
    "InstrumentType": [
        {
            "instrumentTypeName": "Near-infrared fluorescence imager",
            "instrumentTypeIdentifier": {
                "instrumentTypeIdentifierType": "URL",
                "instrumentTypeIdentifierValue": "https://www.licorbio.com/support/answer-portal/imaging-systems/odyssey-classic.html",
            },
        },
        {
            "instrumentTypeName": "Western blot scanner",
            "instrumentTypeIdentifier": {
                "instrumentTypeIdentifierType": "URL",
                "instrumentTypeIdentifierValue": "https://www.licorbio.com/support/answer-portal/imaging-systems/odyssey-classic.html",
            },
        },
        {
            "instrumentTypeName": "Two-color membrane imager",
            "instrumentTypeIdentifier": {
                "instrumentTypeIdentifierType": "URL",
                "instrumentTypeIdentifierValue": "https://www.licorbio.com/support/answer-portal/imaging-systems/odyssey-classic.html",
            },
        },
    ],
    "MeasuredVariable": [
        "Fluorescence intensity at 700 nm",
        "Fluorescence intensity at 800 nm",
    ],
    # Date is editable later. Set to a placeholder commissioning year — update
    # via PUT /api/records/<id>/draft once the real date is known.
    "Date": [
        # Manufactured August 2007 (sticker on back panel); WUR/HAP asset
        # tag ASGHAP20080256 indicates registration in 2008.
        {"Date": "2008", "dateType": "Commissioned"},
    ],
    "RelatedIdentifier": [
        {
            "relatedIdentifierValue": "https://www.licorbio.com/support/answer-portal/imaging-systems/odyssey-classic.html",
            "relatedIdentifierType": "URL",
            "relationType": "IsDescribedBy",
            "relatedIdentifierName": "LI-COR Odyssey Classic product page",
        },
        {
            "relatedIdentifierValue": "https://github.com/PyLabRobot/pylabrobot",
            "relatedIdentifierType": "URL",
            "relationType": "HasComponent",
            "relatedIdentifierName": (
                "PyLabRobot — open-source control software used to drive this "
                "instrument (scan triggering, file retrieval, metadata capture)"
            ),
        },
    ],
    "AlternateIdentifier": [
        {
            # LI-COR serial, read from the back-panel MODEL: 9120 / S/N label.
            "alternateIdentifierValue": "ODY-1576",
            "alternateIdentifierType": "SerialNumber",
        },
        {
            # WUR Animal Science Group / HAP asset tag (2008). b2inst's UI
            # dropdown doesn't expose "InventoryNumber" even though PIDINST
            # allows it, so we use "Other" and put the semantic in the name.
            "alternateIdentifierValue": "ASGHAP20080256",
            "alternateIdentifierType": "Other",
            "alternateIdentifierName": (
                "InventoryNumber — WUR Animal Science Group HAP asset tag"
            ),
        },
        {
            # Secondary inventory / maintenance code (meettech.nl).
            "alternateIdentifierValue": "DDW03040II",
            "alternateIdentifierType": "Other",
            "alternateIdentifierName": (
                "InventoryNumber — secondary maintenance/inventory code"
            ),
        },
    ],
}


def post_draft() -> dict:
    payload = {"metadata": METADATA}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{HOST}/api/records",
        data=body,
        method="POST",
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

    print(f"POST {HOST}/api/records")
    print("Request body:")
    print(json.dumps(payload, indent=2))
    print()

    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"--- HTTP {resp.status} ---")
            return data
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"--- HTTP {e.code} {e.reason} ---")
        try:
            print(json.dumps(json.loads(err_body), indent=2))
        except Exception:
            print(err_body)
        sys.exit(1)


def main() -> None:
    result = post_draft()
    rid = result.get("id")
    links = result.get("links", {})
    print()
    print(f"Draft created.")
    print(f"  Record ID:    {rid}")
    print(f"  Self (API):   {links.get('self')}")
    print(f"  Draft (UI):   {links.get('self_html')}")
    print(f"  Files bucket: {links.get('files')}")
    print()
    print("Inspect the draft via:")
    print(f"  curl -k -H 'Authorization: Bearer $B2INST_TOKEN' \\")
    print(f"       {HOST}/api/records/{rid}/draft | python -m json.tool")


if __name__ == "__main__":
    main()
