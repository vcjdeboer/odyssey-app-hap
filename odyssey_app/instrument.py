"""Instance-card identity for this lab's Odyssey unit.

Project-local. The constants and the instance ``DeviceCard`` below are
THIS lab's specific Odyssey unit. ``plr_v4/`` ships an empty identity
slot on the model-base card; every Odyssey user populates it for their
own unit through an instance card like this one.

Override via the ``ODYSSEY_PID`` and ``ODYSSEY_LANDING_PAGE`` env vars
when a different instrument is attached, or for tests.

Usage::

    from plr_v4.odyssey import OdysseyClassic, ODYSSEY_CLASSIC_BASE
    from odyssey_app.instrument import ODYSSEY_INSTANCE_CARD

    card = ODYSSEY_CLASSIC_BASE.merge(ODYSSEY_INSTANCE_CARD)
    odyssey = OdysseyClassic.from_env(host="...", card=card)
"""

from __future__ import annotations

import os

from plr_v4.device_card import DeviceCard

ODYSSEY_INSTRUMENT_PID = os.environ.get(
    "ODYSSEY_PID",
    "http://hdl.handle.net/21.11157/psf97-zv353",
)

ODYSSEY_INSTRUMENT_LANDING_PAGE = os.environ.get(
    "ODYSSEY_LANDING_PAGE",
    "https://b2inst.gwdg.de/records/psf97-zv353",
)

ODYSSEY_INSTRUMENT_NAME = (
    "LI-COR Odyssey Classic 9120 — Human and Animal Physiology, WUR"
)


ODYSSEY_INSTANCE_CARD = DeviceCard.instance(identity={
    "pid": ODYSSEY_INSTRUMENT_PID,
    "landing_page": ODYSSEY_INSTRUMENT_LANDING_PAGE,
    "name": ODYSSEY_INSTRUMENT_NAME,
})
