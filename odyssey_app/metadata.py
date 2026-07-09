"""Western blot metadata schema for reproducible imaging.

Every scan produces a structured record that captures the full
experimental context: instrument, scan settings, antibodies, samples,
and provenance. This is what turns an Odyssey TIFF into a traceable,
reproducible measurement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from odyssey_app.instrument import ODYSSEY_INSTRUMENT_PID


@dataclass
class Antibody:
    """Antibody used in the Western blot."""

    target: str = ""              # "beta-actin", "phospho-ERK"
    host: str = ""                # "rabbit", "mouse"
    clonality: str = ""           # "monoclonal", "polyclonal"
    hap_id: str = ""              # "HAP-0421" (internal HAP database ID,
                                  # canonical identifier — resolves to
                                  # vendor+catalog+lot automatically)
    vendor: str = ""              # "Cell Signaling Technology"
    catalog: str = ""             # "#4970"
    lot: str = ""                 # "GR3456789-1"
    rrid: str = ""                # "AB_2315049"
    dilution: str = ""            # "1:1000"
    channel: int = 700            # 700 or 800
    dye: str = ""                 # "IRDye 800CW", "IRDye 680RD"
    incubation: str = ""          # "overnight 4°C", "1h RT"


@dataclass
class LaneSample:
    """Sample loaded in a single lane."""

    lane: int = 1
    name: str = ""                # "HeLa lysate"
    treatment: str = ""           # "10 nM EGF, 15 min"
    loading_ug: Optional[float] = None  # protein loaded in µg
    is_ladder: bool = False       # molecular weight marker


@dataclass
class BlockingCondition:
    """Membrane blocking conditions."""

    buffer: str = ""              # "5% milk in TBST"
    duration: str = ""            # "1h RT"


@dataclass
class ScanSettings:
    """Scan parameters as sent to the Odyssey."""

    resolution_um: int = 169
    quality: str = "medium"
    intensity_700: float = 5.0
    intensity_800: float = 5.0
    channel_700: bool = True
    channel_800: bool = True
    origin_x: int = 0
    origin_y: int = 0
    width_cm: int = 10
    height_cm: int = 10
    focus_offset_mm: float = 0.0


@dataclass
class WesternBlotRecord:
    """Complete metadata record for a Western blot scan.

    Captures everything needed to reproduce the experiment:
    instrument identity, scan parameters, antibodies, samples,
    and operator provenance.
    """

    # -- instrument identity (PIDInst) --
    # Defaults to the configured instrument PID (Handle URI) for this
    # lab's Odyssey unit; see odyssey_app/instrument.py.
    instrument_pid: str = field(default_factory=lambda: ODYSSEY_INSTRUMENT_PID)
    instrument_model: str = "Odyssey Classic 9120"
    microscope_serial: str = ""
    software_version: str = ""

    # -- scan --
    scan_name: str = ""
    scan_group: str = "public"
    scan_settings: ScanSettings = field(default_factory=ScanSettings)
    scan_timestamp: str = ""
    scan_duration_s: float = 0.0

    # -- sample preparation --
    samples: list[LaneSample] = field(default_factory=list)
    membrane_type: str = ""           # "nitrocellulose", "PVDF"
    gel_type: str = ""                # "4-12% Bis-Tris"
    transfer_method: str = ""         # "wet", "semi-dry", "iBlot"

    # -- antibodies --
    primary_antibodies: list[Antibody] = field(default_factory=list)
    secondary_antibodies: list[Antibody] = field(default_factory=list)
    blocking: BlockingCondition = field(default_factory=BlockingCondition)

    # -- provenance --
    operator: str = ""
    project: str = ""
    experiment_id: str = ""
    protocol_ref: str = ""            # DOI or local protocol ID
    notes: str = ""

    # -- display (saved separately from raw data) --
    display_settings: dict = field(default_factory=dict)

    def stamp_timestamp(self) -> None:
        """Set scan_timestamp to current ISO 8601 time."""
        self.scan_timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: dict) -> WesternBlotRecord:
        """Reconstruct from a dictionary."""
        record = cls()
        # Simple fields
        for key in (
            "instrument_pid", "instrument_model", "microscope_serial",
            "software_version", "scan_name", "scan_group", "scan_timestamp",
            "scan_duration_s", "membrane_type", "gel_type", "transfer_method",
            "operator", "project", "experiment_id", "protocol_ref", "notes",
        ):
            if key in data:
                setattr(record, key, data[key])

        if "scan_settings" in data:
            record.scan_settings = ScanSettings(**data["scan_settings"])
        if "blocking" in data:
            record.blocking = BlockingCondition(**data["blocking"])
        if "samples" in data:
            record.samples = [LaneSample(**s) for s in data["samples"]]
        if "primary_antibodies" in data:
            record.primary_antibodies = [Antibody(**a) for a in data["primary_antibodies"]]
        if "secondary_antibodies" in data:
            record.secondary_antibodies = [Antibody(**a) for a in data["secondary_antibodies"]]
        if "display_settings" in data:
            record.display_settings = data["display_settings"]
        return record

    @classmethod
    def from_json(cls, json_str: str) -> WesternBlotRecord:
        return cls.from_dict(json.loads(json_str))
