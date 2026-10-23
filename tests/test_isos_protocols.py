"""Tests for the ISOS protocol registry (app/measurement/isos_protocols.py).

The registry is the single source of truth for which protocols the GUI offers
and what each one requires. These tests pin the registry contract: the three
implemented protocols, the lookup/listing helpers, and ProtocolSpec immutability.
"""

import dataclasses

import pytest

from app.measurement.isos_protocols import (
    PROTOCOLS,
    ProtocolSpec,
    get_protocol,
    list_protocol_ids,
    list_protocol_labels,
)

# The protocols that currently have runner support and must stay registered.
IMPLEMENTED_IDS = ["ISOS-D-1", "ISOS-L-1", "ISOS-V-1"]


def test_implemented_protocols_registered():
    assert list_protocol_ids() == IMPLEMENTED_IDS


def test_labels_match_ids_order():
    assert list_protocol_labels() == [s.label for s in PROTOCOLS]
    # registration order is preserved across both helpers
    assert list_protocol_labels() == IMPLEMENTED_IDS


@pytest.mark.parametrize("pid", IMPLEMENTED_IDS)
def test_get_protocol_returns_matching_spec(pid):
    spec = get_protocol(pid)
    assert isinstance(spec, ProtocolSpec)
    assert spec.id == pid


def test_get_protocol_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        get_protocol("ISOS-D-99")


def test_protocolspec_is_frozen():
    spec = get_protocol("ISOS-L-1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.label = "tampered"  # type: ignore[misc]


def test_continuous_flags_consistent_with_runner_support():
    # D-1 is manual-trigger; L-1 and V-1 are continuous.
    assert get_protocol("ISOS-D-1").is_continuous is False
    assert get_protocol("ISOS-L-1").is_continuous is True
    assert get_protocol("ISOS-V-1").is_continuous is True


def test_v1_requires_dark_jv_l1_does_not():
    assert get_protocol("ISOS-V-1").requires_dark_jv is True
    assert get_protocol("ISOS-L-1").requires_dark_jv is False


def test_stress_with_light_matches_protocol_intent():
    # L-1 stresses under illumination; D-1/V-1 stress in the dark.
    assert get_protocol("ISOS-L-1").stress_with_light is True
    assert get_protocol("ISOS-D-1").stress_with_light is False
    assert get_protocol("ISOS-V-1").stress_with_light is False


def test_all_specs_have_positive_defaults():
    for spec in PROTOCOLS:
        assert spec.default_interval_min > 0
        assert spec.default_total_duration_h > 0
        assert spec.requires_illuminated_jv is True
