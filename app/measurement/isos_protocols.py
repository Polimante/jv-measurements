"""
ISOS protocol registry.

A small declarative catalogue of the ISOS stability protocols the application
knows about. This is scaffolding only — registering a protocol here does not
add a measurement runner. ISOS-L-1 and ISOS-V-1 are added in later sessions.

Usage:
    from app.measurement.isos_protocols import (
        get_protocol, list_protocol_ids, list_protocol_labels,
    )
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtocolSpec:
    """Declarative description of a single ISOS protocol."""

    id: str
    label: str
    is_continuous: bool
    supports_oc: bool
    supports_mpp: bool
    supports_bias_v1: bool
    requires_dark_jv: bool
    requires_illuminated_jv: bool
    default_interval_min: float
    default_total_duration_h: float
    stress_with_light: bool
    notes: str


# ── Registry ──────────────────────────────────────────────────────────────────
# Only ISOS-D-1 is registered for now. L-1 and V-1 are added in later sessions.
PROTOCOLS = [
    ProtocolSpec(
        id="ISOS-D-1",
        label="ISOS-D-1",
        is_continuous=False,
        supports_oc=True,
        supports_mpp=False,
        supports_bias_v1=False,
        requires_dark_jv=False,
        requires_illuminated_jv=True,
        default_interval_min=60.0,
        default_total_duration_h=24.0,
        stress_with_light=False,
        notes=(
            "Dark storage / shelf life. Cell is kept at open circuit in the "
            "dark between periodic illuminated J-V characterisations."
        ),
    ),
    ProtocolSpec(
        id="ISOS-L-1",
        label="ISOS-L-1",
        is_continuous=True,
        supports_oc=True,
        supports_mpp=True,
        supports_bias_v1=False,
        requires_dark_jv=False,
        requires_illuminated_jv=True,
        default_interval_min=15.0,
        default_total_duration_h=24.0,
        stress_with_light=True,
        notes="Light soaking at ambient T, OC or MPP load.",
    ),
]


def get_protocol(protocol_id: str) -> ProtocolSpec:
    """Return the ProtocolSpec for ``protocol_id`` or raise KeyError."""
    for spec in PROTOCOLS:
        if spec.id == protocol_id:
            return spec
    raise KeyError(f"Unknown ISOS protocol id: {protocol_id!r}")


def list_protocol_ids() -> list:
    """Return all registered protocol ids, in registration order."""
    return [spec.id for spec in PROTOCOLS]


def list_protocol_labels() -> list:
    """Return all registered protocol labels, in registration order."""
    return [spec.label for spec in PROTOCOLS]
