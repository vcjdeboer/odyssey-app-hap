"""DeviceCard — two-layer device description.

Captures per-unit hardware configuration: what THIS specific instrument has
installed, which may differ from another unit of the same model.

Two layers:
    Model base  — what every unit of this model always has (constant per model)
    Instance    — what THIS specific unit has (optional modules, syringe size, etc.)

The effective card = base.merge(instance).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeviceCard:
    """Machine-readable device description with merge and introspection."""

    name: str = ""
    vendor: str = ""
    model: str = ""
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)
    connection: dict[str, Any] = field(default_factory=dict)
    # Per-unit identity (PID, landing page, friendly name). Empty by
    # default — model-base cards leave it for the user to populate at
    # the instance layer (e.g. with their PIDInst Handle URI).
    identity: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def instance(cls, **kwargs) -> DeviceCard:
        """Create a partial card for instance-level overrides."""
        return cls(**kwargs)

    def merge(self, other: DeviceCard) -> DeviceCard:
        """Deep-merge instance config on top of model base. Returns new card."""
        merged_caps = copy.deepcopy(self.capabilities)
        for cap_name, cap_data in other.capabilities.items():
            if cap_name in merged_caps:
                merged_caps[cap_name].update(cap_data)
            else:
                merged_caps[cap_name] = copy.deepcopy(cap_data)

        merged_conn = {**self.connection, **other.connection}
        merged_identity = {**self.identity, **other.identity}

        return DeviceCard(
            name=other.name or self.name,
            vendor=other.vendor or self.vendor,
            model=other.model or self.model,
            capabilities=merged_caps,
            connection=merged_conn,
            identity=merged_identity,
        )

    def has(self, capability: str) -> bool:
        """Does this device have a capability?"""
        return capability in self.capabilities

    def get(self, capability: str, key: str, default: Any = None) -> Any:
        """Get a feature flag or spec value for a capability."""
        return self.capabilities.get(capability, {}).get(key, default)

    def features(self, capability: str) -> dict[str, bool]:
        """All boolean feature flags for a capability."""
        cap = self.capabilities.get(capability, {})
        return {k: v for k, v in cap.items() if isinstance(v, bool)}

    def specs(self, capability: str) -> dict[str, Any]:
        """All non-boolean specs for a capability."""
        cap = self.capabilities.get(capability, {})
        return {k: v for k, v in cap.items() if not isinstance(v, bool)}

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "name": self.name,
            "vendor": self.vendor,
            "model": self.model,
            "capabilities": copy.deepcopy(self.capabilities),
            "connection": copy.deepcopy(self.connection),
            "identity": copy.deepcopy(self.identity),
        }

    @classmethod
    def from_dict(cls, data: dict) -> DeviceCard:
        """Reconstruct from a dictionary (e.g., loaded from JSON file)."""
        return cls(
            name=data.get("name", ""),
            vendor=data.get("vendor", ""),
            model=data.get("model", ""),
            capabilities=data.get("capabilities", {}),
            connection=data.get("connection", {}),
            identity=data.get("identity", {}),
        )

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> DeviceCard:
        """Reconstruct from JSON string."""
        return cls.from_dict(json.loads(json_str))
