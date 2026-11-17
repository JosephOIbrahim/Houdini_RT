"""Installed material-policy validation; USD semantic cases run separately in hython."""
import copy
import json
from pathlib import Path
import pytest
from rt_runtime.material_support import scalar, validate_policy
from rt_runtime.scene_support import load_profile

FIXTURE = Path(__file__).parent / "fixtures" / "material_support"


def test_material_policy_is_loaded_from_installed_profile_and_matches_native_source():
    profile = load_profile({"HRT_PLUGIN_ROOT": str(FIXTURE)})
    assert profile.materials["surface_shader"] == "ND_open_pbr_surface_surfaceshader"
    source = Path(__file__).resolve().parents[3] / "src" / "hdRT" / "capabilities.json"
    if source.is_file():
        assert profile.materials == json.loads(source.read_text())["material_support"]


@pytest.mark.parametrize("field,value", [("model", "other"), ("render_contexts", [""]),
    ("surface_shader", "UsdPreviewSurface"), ("parameters", {}), ("required_defaults", []),
    ("unsupported_explicit_inputs", False), ("neutral_displacement", {}), ("connections", "supported")])
def test_malformed_or_unknown_material_profiles_fail_closed(field, value):
    policy = copy.deepcopy(load_profile({"HRT_PLUGIN_ROOT": str(FIXTURE)}).materials)
    policy[field] = value
    with pytest.raises(ValueError): validate_policy(policy)


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), 1e100, "0.5"])
def test_scalar_validation_rejects_native_unrepresentable_values(value):
    with pytest.raises(ValueError): scalar(value)
