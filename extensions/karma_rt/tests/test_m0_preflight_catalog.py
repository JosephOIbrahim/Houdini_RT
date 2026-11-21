"""M0 Verification: Live Houdini 22.0.* Catalog, Preflight Doctor & Zero-Restart Presets."""
from __future__ import annotations

import json
import unittest

import hou

from karma_rt.catalog import load_h22_catalog, resolve_delegate_label
from karma_rt.hardware import probe_hardware
from karma_rt.preflight import run_preflight
from karma_rt.presets import (
    AUTO_TURBO_OVERRIDES,
    FOCUS_CROP_REGIONS,
    build_denoise_imagefilter_json,
    build_focus_crop_override,
    build_pixel_oracle_json,
    build_resolution_scale_override,
    get_presets,
    strip_builtin_denoiser_if_usd_image_filters,
    validate_overrides_zero_restart,
    validate_preset_zero_restart,
)


class TestM0PreflightAndCatalog(unittest.TestCase):
    def setUp(self) -> None:
        hou.hipFile.clear(suppress_save_prompt=True)

    def test_catalog_reads_live_usd_renderers_and_wildcards(self) -> None:
        cat = load_h22_catalog(hou_module=hou)
        self.assertNotEqual(cat.source, "fallback", "Must read live $HFS/houdini/UsdRenderers.json")
        self.assertTrue(cat.is_restart_attribute("karma:global:engine"))
        self.assertTrue(cat.is_restart_attribute("karma:global:dicingqualityscale"))
        self.assertTrue(cat.is_restart_attribute("karma:global:overrideobject"))
        self.assertTrue(cat.is_restart_attribute("karma:object:diffuselimit"))
        self.assertTrue(cat.is_restart_attribute("disableMotionBlur"))
        self.assertFalse(cat.is_restart_attribute("karma:global:ipr_proxydepth"))
        self.assertFalse(cat.is_restart_attribute("karma:global:ipr_reserve_threads"))
        self.assertFalse(cat.is_restart_attribute("karma:global:pathtracedsamples"))
        self.assertFalse(cat.is_restart_attribute("karma:global:pixeloracle"))
        self.assertFalse(cat.is_restart_attribute("karma:global:imagefilter"))
        self.assertFalse(cat.is_restart_attribute("disableDepthOfField"))

        # Live H22 LOP node types probed read-only
        self.assertTrue(cat.verified_node_types.get("karmarendersettings", False))
        self.assertTrue(cat.verified_node_types.get("light::2.0", False))
        self.assertTrue(cat.verified_node_types.get("scatterinstances", False))
        self.assertTrue(cat.verified_node_types.get("imagefilter", False))

    def test_delegate_label_resolution(self) -> None:
        labels = ("Houdini VK", "Karma CPU", "Karma XPU", "Storm")
        self.assertEqual(resolve_delegate_label(labels, "BRAY_HdKarmaXPU"), "Karma XPU")
        self.assertEqual(resolve_delegate_label(labels, "Karma XPU"), "Karma XPU")
        self.assertEqual(resolve_delegate_label(labels, "BRAY_HdKarma"), "Karma CPU")
        self.assertEqual(resolve_delegate_label(labels, "HD_HoudiniRendererPlugin"), "Houdini VK")

    def test_all_presets_and_autoturbo_strictly_zero_restart(self) -> None:
        cat = load_h22_catalog(hou_module=hou)
        presets = get_presets("optix")
        self.assertIn("live_draft", presets)
        self.assertIn("interactive_lookdev", presets)
        self.assertIn("clay_headlight", presets)
        self.assertIn("final_parity", presets)
        self.assertIn("vulkan_layout", presets)

        for preset in presets.values():
            violations = validate_preset_zero_restart(preset, cat)
            self.assertEqual(violations, [], f"Preset {preset.id} had restart attrs: {violations}")

        turbo_violations = validate_overrides_zero_restart(AUTO_TURBO_OVERRIDES, cat, allow_restart=False)
        self.assertEqual(turbo_violations, [])

        # Confirm v2.0 physics levers on Live Draft, Lookdev, & Clay
        draft_ov = presets["live_draft"].usd_overrides
        self.assertEqual(draft_ov["karma:global:pathtracedsamples"], ("int", 9))
        self.assertEqual(draft_ov["karma:global:colorlimitindlinked"], ("bool", False))
        self.assertEqual(draft_ov["karma:global:convergence_mode"], ("token", "Path Traced"))
        self.assertEqual(draft_ov["karma:global:mtlx_imagewidth"], ("float", 2.0))
        self.assertEqual(draft_ov["karma:global:offscreenquality"], ("float", 0.02))
        self.assertEqual(draft_ov["instantaneousShutter"], ("bool", True))

        clay_ov = presets["clay_headlight"].usd_overrides
        self.assertEqual(clay_ov["karma:global:override_lighting"], ("token", "Headlight"))
        self.assertEqual(clay_ov["karma:global:head_ao_samples"], ("int", 4))

        # Focus Crop & Resolution Scale must also be strictly zero-restart
        for crop_key in FOCUS_CROP_REGIONS:
            crop_ov = build_focus_crop_override(crop_key)
            self.assertEqual(validate_overrides_zero_restart(crop_ov, cat, allow_restart=False), [])
        scale_ov = build_resolution_scale_override((1280, 720), 0.5)
        self.assertEqual(scale_ov, {"resolution": ("int2", (640, 360))})
        self.assertEqual(validate_overrides_zero_restart(scale_ov, cat, allow_restart=False), [])

        # Double-denoiser protection strips built-in imagefilter when USD Image Filters exist
        stripped = strip_builtin_denoiser_if_usd_image_filters(draft_ov, has_usd_image_filters=True)
        self.assertNotIn("karma:global:imagefilter", stripped)
        kept = strip_builtin_denoiser_if_usd_image_filters(draft_ov, has_usd_image_filters=False)
        self.assertIn("karma:global:imagefilter", kept)

        # Confirm exact live-verified JSON formats including perceptual OCIO mode=1
        oracle = json.loads(build_pixel_oracle_json("variance", minrays=8, variance=0.02))
        self.assertEqual(oracle[0], "variance")
        self.assertEqual(oracle[1]["minrays"], 8)
        self.assertEqual(oracle[1]["ociomode"], 1)
        self.assertAlmostEqual(oracle[1]["variance"], 0.02)

        denoise = json.loads(build_denoise_imagefilter_json("optix"))
        self.assertEqual(denoise[0][0], "denoise")
        self.assertEqual(denoise[0][1]["engine"], "optix")
        self.assertEqual(denoise[0][1]["aovs"], ["C"])

        # Light sampling mode must use exact live token 'Light Tree'
        lookdev_lsm = presets["interactive_lookdev"].usd_overrides["karma:global:light_sampling_mode"]
        self.assertEqual(lookdev_lsm[1], "Light Tree")
        self.assertEqual(presets["interactive_lookdev"].usd_overrides["karma:global:pathtracedsamples"], ("int", 36))

        # Hardware probe returns structured HardwareSnapshot without failing
        hw = probe_hardware()
        self.assertIsInstance(hw.summary_badge, str)
        self.assertTrue(len(hw.summary_badge) > 0)

    def test_preflight_doctor_is_truthful_and_clean(self) -> None:
        unsaved_before = hou.hipFile.hasUnsavedChanges()
        nodes_before = tuple(n.path() for n in hou.node("/").allSubChildren())
        stage_before = tuple(n.path() for n in hou.node("/stage").children())

        report = run_preflight(hou_module=hou)

        self.assertEqual(
            hou.hipFile.hasUnsavedChanges(),
            unsaved_before,
            "Preflight must NEVER change .hip dirty state",
        )
        self.assertEqual(
            tuple(n.path() for n in hou.node("/").allSubChildren()),
            nodes_before,
            "Preflight must NEVER create or leave any nodes in the scene",
        )
        self.assertEqual(
            tuple(n.path() for n in hou.node("/stage").children()),
            stage_before,
        )
        ver = tuple(hou.applicationVersion())
        self.assertEqual(ver[0], 22)
        self.assertEqual(report.reference_grid_guard_needed, ver == (22, 0, 429))
        self.assertTrue(report.karma_xpu_available)
        self.assertTrue(report.karma_cpu_available)
        self.assertTrue(report.vulkan_available)
        self.assertIn(report.status, ("READY", "DEGRADED"))
        d = report.to_dict()
        self.assertEqual(d["status"], report.status)
        self.assertIn("checks", d)


if __name__ == "__main__":
    unittest.main()
