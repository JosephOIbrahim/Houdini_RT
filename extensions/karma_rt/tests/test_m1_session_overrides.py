"""M1 Verification: Non-Destructive LOP:Custom Session Overrides, Cook Invariance & HIP Purity."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

import hou
from pxr import Sdf

from karma_rt.catalog import load_h22_catalog
from karma_rt.presets import AUTO_TURBO_OVERRIDES, get_presets
from karma_rt.session_overrides import (
    EPHEMERAL_SETTINGS_PATH,
    PROVENANCE_KEY,
    HipLifecycleGuard,
    SessionOverrideEngine,
)


def _build_solaris_shot_stage():
    """Create a realistic Node-Flow-style /shot/{geo,LGT,MTL,cam} + Karma setup stage."""
    stage_net = hou.node("/stage")
    prim_lop = stage_net.createNode("primitive", "shot_root")
    prim_lop.parm("primpath").set("/shot/geo")
    prim_lop.parm("primkind").set("group")

    cam_lop = stage_net.createNode("camera", "shot_cam")
    cam_lop.parm("primpath").set("/shot/cam/main")
    cam_lop.setInput(0, prim_lop)

    krs = stage_net.createNode("karmarendersettings", "karmarendersettings")
    krs.setInput(0, cam_lop)
    krs.parm("pathtracedsamples").set(128)
    krs.parm("camera").set("/shot/cam/main")

    rop = stage_net.createNode("usdrender_rop", "usdrender_rop")
    rop.setInput(0, krs)

    krs.setDisplayFlag(True)
    # Initial cook so baseline cookCount is established
    _ = krs.stage(apply_viewport_overrides=True)
    return stage_net, krs, rop


class TestM1SessionOverrides(unittest.TestCase):
    def setUp(self) -> None:
        hou.hipFile.clear(suppress_save_prompt=True)
        self.cat = load_h22_catalog(hou_module=hou)
        self.engine = SessionOverrideEngine(hou_module=hou)

    def test_nondestructive_lifecycle_cook_invariance_and_artist_preservation(self) -> None:
        stage_net, krs, _rop = _build_solaris_shot_stage()
        initial_children = len(stage_net.children())
        initial_cook = krs.cookCount()

        # Author an unrelated artist override on LOP:Custom that MUST survive KarmaRT clear!
        with stage_net.viewportOverrides(
            krs, output_index=0, active_layer=hou.lopViewportOverridesLayer.Custom
        ) as ov:
            layer = ov.layer()
            with Sdf.ChangeBlock():
                artist_prim = Sdf.CreatePrimInLayer(layer, "/shot/geo")
                artist_prim.specifier = Sdf.SpecifierOver
                artist_attr = Sdf.AttributeSpec(artist_prim, "artist:keep_me", Sdf.ValueTypeNames.Float)
                artist_attr.default = 42.0

        presets = get_presets("optix")
        draft_overrides = presets["live_draft"].usd_overrides
        lookdev_overrides = presets["interactive_lookdev"].usd_overrides

        # 1. Dry-run preview must not mutate USD or cookCount
        preview = self.engine.preview_overrides(stage_net, draft_overrides, "live_draft", catalog=self.cat, display_lop=krs)
        self.assertTrue(preview.valid)
        self.assertEqual(krs.cookCount(), initial_cook)
        self.assertEqual(self.engine.list_owned_specs(stage_net, display_lop=krs), [])

        # 2. Apply Live Draft
        r1 = self.engine.apply_overrides(
            stage_net, draft_overrides, "live_draft", "sess-1", catalog=self.cat, display_lop=krs
        )
        self.assertTrue(r1.applied, f"Apply failed: {r1.error} mismatches={r1.mismatches}")
        self.assertEqual(r1.mismatches, {})
        self.assertEqual(r1.cook_count_after - r1.cook_count_before, 0, "LOP:Custom apply must cause ZERO upstream recooks!")
        self.assertEqual(len(stage_net.children()), initial_children, "Must NEVER splice helper LOP nodes!")

        # Verify viewport stage sees Draft v2.0 values while production stage stays 100% untouched!
        vp_vals = self.engine.read_stage_values(
            krs,
            r1.binding.settings_prim_path,
            [
                "karma:global:pathtracedsamples",
                "karma:global:ipr_proxydepth",
                "karma:global:colorlimitindlinked",
                "karma:global:convergence_mode",
                "karma:global:mtlx_imagewidth",
                "karma:global:offscreenquality",
                "instantaneousShutter",
                "karma:global:pixeloracle",
                "karma:global:imagefilter",
            ],
            apply_viewport_overrides=True,
        )
        prod_vals = self.engine.read_stage_values(
            krs,
            r1.binding.settings_prim_path,
            ["karma:global:pathtracedsamples", "karma:global:ipr_proxydepth", "karma:global:colorlimitindlinked"],
            apply_viewport_overrides=False,
        )
        self.assertEqual(vp_vals["karma:global:pathtracedsamples"], 9)
        self.assertEqual(vp_vals["karma:global:ipr_proxydepth"], 3)
        self.assertIs(vp_vals["karma:global:colorlimitindlinked"], False)
        self.assertEqual(vp_vals["karma:global:convergence_mode"], "Path Traced")
        self.assertAlmostEqual(vp_vals["karma:global:mtlx_imagewidth"], 2.0, places=3)
        self.assertAlmostEqual(vp_vals["karma:global:offscreenquality"], 0.02, places=3)
        self.assertIs(vp_vals["instantaneousShutter"], True)
        self.assertEqual(json.loads(vp_vals["karma:global:pixeloracle"])[0], "variance")
        self.assertEqual(json.loads(vp_vals["karma:global:pixeloracle"])[1]["ociomode"], 1)
        self.assertEqual(json.loads(vp_vals["karma:global:imagefilter"])[0][0], "denoise")
        self.assertEqual(prod_vals["karma:global:pathtracedsamples"], 128, "Production stage MUST remain untouched!")
        self.assertIsNone(prod_vals["karma:global:ipr_proxydepth"], "Viewport IPR attrs MUST NOT leak to production stage!")
        self.assertIs(prod_vals["karma:global:colorlimitindlinked"], True, "Production colorlimitindlinked MUST remain untouched!")
        self.assertEqual(krs.parm("pathtracedsamples").eval(), 128, "karmarendersettings LOP parm MUST remain untouched!")

        # 3. Switch to Interactive Lookdev -> verify stale Draft-only attrs cleaned & cookCount delta == 0
        r2 = self.engine.apply_overrides(
            stage_net, lookdev_overrides, "interactive_lookdev", "sess-1", catalog=self.cat, display_lop=krs
        )
        self.assertTrue(r2.applied, f"Lookdev apply failed: {r2.error}")
        self.assertEqual(r2.cook_count_after, initial_cook)
        vp_vals2 = self.engine.read_stage_values(
            krs,
            r2.binding.settings_prim_path,
            ["karma:global:pathtracedsamples", "karma:global:light_sampling_mode", "karma:global:colorlimitindirect"],
            apply_viewport_overrides=True,
        )
        self.assertEqual(vp_vals2["karma:global:pathtracedsamples"], 36)
        self.assertEqual(vp_vals2["karma:global:light_sampling_mode"], "Light Tree")

        # 4. Apply Auto-Turbo boost -> verify cookCount delta == 0
        merged_turbo = dict(lookdev_overrides)
        merged_turbo.update(AUTO_TURBO_OVERRIDES)
        r3 = self.engine.apply_overrides(
            stage_net, merged_turbo, "interactive_lookdev+turbo", "sess-1", catalog=self.cat, display_lop=krs
        )
        self.assertTrue(r3.applied)
        self.assertEqual(r3.cook_count_after, initial_cook)
        vp_vals3 = self.engine.read_stage_values(
            krs, r3.binding.settings_prim_path, ["karma:global:ipr_proxydepth", "karma:global:percentofsamples"], apply_viewport_overrides=True
        )
        self.assertEqual(vp_vals3["karma:global:ipr_proxydepth"], 4)
        self.assertAlmostEqual(vp_vals3["karma:global:percentofsamples"], 25.0, places=3)

        # 5. Surgical Clear -> removes ONLY KarmaRT specs; preserves artist:keep_me == 42.0!
        clear_info = self.engine.clear_owned_overrides(stage_net, display_lop=krs)
        self.assertGreater(len(clear_info["removed_specs"]), 0)
        self.assertEqual(self.engine.list_owned_specs(stage_net, display_lop=krs), [])
        self.assertEqual(krs.cookCount(), initial_cook, "Clear must ALSO produce cookCount delta == 0!")

        # Confirm artist's unrelated LOP:Custom spec survived!
        artist_vals = self.engine.read_stage_values(krs, "/shot/geo", ["artist:keep_me"], apply_viewport_overrides=True)
        self.assertAlmostEqual(artist_vals["artist:keep_me"], 42.0, places=4)

        # Confirm viewport stage reverted to production 128 samples!
        reverted_vals = self.engine.read_stage_values(
            krs, r1.binding.settings_prim_path, ["karma:global:pathtracedsamples"], apply_viewport_overrides=True
        )
        self.assertEqual(reverted_vals["karma:global:pathtracedsamples"], 128)

    def test_restart_attribute_blocked_unless_explicitly_allowed(self) -> None:
        stage_net, krs, _ = _build_solaris_shot_stage()
        bad_overrides = {"karma:global:dicingqualityscale": ("float", 0.5)}
        preview = self.engine.preview_overrides(stage_net, bad_overrides, "bad", catalog=self.cat, display_lop=krs)
        self.assertFalse(preview.valid)
        receipt = self.engine.apply_overrides(
            stage_net, bad_overrides, "bad", "sess-2", catalog=self.cat, display_lop=krs, allow_restart=False
        )
        self.assertFalse(receipt.applied)
        self.assertIn("dicingqualityscale", receipt.error)
        self.assertEqual(self.engine.list_owned_specs(stage_net, display_lop=krs), [])

    def test_ephemeral_settings_prim_when_stage_has_no_krs(self) -> None:
        stage_net = hou.node("/stage")
        prim_lop = stage_net.createNode("primitive", "lookdev_root")
        prim_lop.parm("primpath").set("/shot/geo/lookdev_mesh")
        prim_lop.setDisplayFlag(True)
        _ = prim_lop.stage(apply_viewport_overrides=True)

        draft = get_presets("optix")["live_draft"].usd_overrides
        receipt = self.engine.apply_overrides(stage_net, draft, "live_draft", "sess-eph", catalog=self.cat, display_lop=prim_lop)
        self.assertTrue(receipt.applied, f"Ephemeral apply failed: {receipt.error}")
        self.assertEqual(receipt.binding.settings_prim_path, EPHEMERAL_SETTINGS_PATH)
        self.assertEqual(receipt.binding.source, "ephemeral_session")

        # Production stage must NOT contain the ephemeral prim
        prod_stage = prim_lop.stage(apply_viewport_overrides=False)
        self.assertFalse(prod_stage.GetPrimAtPath(EPHEMERAL_SETTINGS_PATH).IsValid())
        _ = prim_lop.stage(apply_viewport_overrides=True)

        # Clear removes ephemeral prim and root metadata completely
        self.engine.clear_owned_overrides(stage_net, display_lop=prim_lop)
        self.assertEqual(self.engine.list_owned_specs(stage_net, display_lop=prim_lop), [])
        vp_stage = prim_lop.stage(apply_viewport_overrides=True)
        self.assertFalse(vp_stage.GetPrimAtPath(EPHEMERAL_SETTINGS_PATH).IsValid())

    def test_hip_lifecycle_guard_keeps_saved_hip_pure(self) -> None:
        stage_net, krs, _ = _build_solaris_shot_stage()
        draft = get_presets("optix")["live_draft"].usd_overrides
        r1 = self.engine.apply_overrides(stage_net, draft, "live_draft", "sess-hip", catalog=self.cat, display_lop=krs)
        self.assertTrue(r1.applied)

        guard = HipLifecycleGuard(self.engine, hou_module=hou)
        guard.install()
        try:
            guard.register_active(
                stage_net.path(),
                {
                    "overrides": dict(draft),
                    "preset_id": "live_draft",
                    "session_id": "sess-hip",
                    "display_lop_path": krs.path(),
                },
            )

            with tempfile.TemporaryDirectory() as tmp:
                hip_path = os.path.join(tmp, "karmart_purity_test.hip").replace("\\", "/")
                hou.hipFile.save(hip_path)

                # Live session must have been restored seamlessly by AfterSave!
                self.assertGreater(
                    len(self.engine.list_owned_specs(stage_net, display_lop=krs)),
                    0,
                    "AfterSave must restore active viewport overrides automatically",
                )

                # Load saved .hip into fresh session and verify ZERO KarmaRT specs were saved!
                guard.uninstall()
                hou.hipFile.load(hip_path, suppress_save_prompt=True)
                loaded_stage = hou.node("/stage")
                loaded_krs = loaded_stage.displayNode()
                owned_in_saved_hip = self.engine.list_owned_specs(loaded_stage, display_lop=loaded_krs)
                self.assertEqual(
                    owned_in_saved_hip,
                    [],
                    f"Saved .hip MUST contain ZERO {PROVENANCE_KEY} specs, found: {owned_in_saved_hip}",
                )
        finally:
            guard.uninstall()

    def test_v2_focus_crop_res_scale_solo_and_usd_image_filter_detection(self) -> None:
        stage_net, krs, _ = _build_solaris_shot_stage()
        # Add two geometry prims and two lights so Solo Geometry / Solo Lights work on real USD prims
        sph = stage_net.createNode("sphere", "hero")
        cub = stage_net.createNode("cube", "bg")
        l1 = stage_net.createNode("light::2.0", "key_lgt")
        l2 = stage_net.createNode("light::2.0", "fill_lgt")
        mrg = stage_net.createNode("merge", "merge_all")
        for idx, n in enumerate((krs.inputs()[0], sph, cub, l1, l2)):
            mrg.setInput(idx, n)
        krs.setInput(0, mrg)
        _ = krs.stage(apply_viewport_overrides=True)
        cook_base = krs.cookCount()

        # Verify production resolution readback
        rs_path = self.engine.resolve_binding(stage_net, display_lop=krs).settings_prim_path
        self.assertEqual(self.engine.read_production_resolution(krs, rs_path), (1280, 720))
        self.assertFalse(self.engine.has_usd_image_filters(krs, rs_path))

        # Apply dataWindowNDC (float4) and scaled resolution (int2) on LOP:Custom -> verify readback + prod untouched!
        overrides = {
            "dataWindowNDC": ("float4", (0.25, 0.25, 0.75, 0.75)),
            "resolution": ("int2", (640, 360)),
        }
        receipt = self.engine.apply_overrides(
            stage_net, overrides, "crop_scale_test", "sess-v2", catalog=self.cat, display_lop=krs
        )
        self.assertTrue(receipt.applied, f"Failed: {receipt.error}")
        self.assertEqual(receipt.mismatches, {}, f"Type coercion mismatches: {receipt.mismatches}")
        self.assertEqual(receipt.cook_count_after, cook_base)
        self.assertEqual(receipt.verified_values["resolution"], (640, 360))
        self.assertEqual(receipt.verified_values["dataWindowNDC"], (0.25, 0.25, 0.75, 0.75))
        # Production resolution remains 1280x720!
        self.assertEqual(self.engine.read_production_resolution(krs, rs_path), (1280, 720))

        # Verify non-destructive Solo Geometry & Solo Lights
        solo_geo = self.engine.set_solo_geometry(stage_net, ["/hero"], display_lop=krs)
        self.assertIn("/hero", solo_geo)
        solo_lgt = self.engine.set_solo_lights(stage_net, ["/lights/key_lgt"], display_lop=krs)
        self.assertIn("/lights/key_lgt", solo_lgt)
        cleared_solo = self.engine.clear_solo_overrides(stage_net, display_lop=krs)
        self.assertEqual(cleared_solo["geometry"], [])
        self.assertEqual(cleared_solo["lights"], [])
        self.assertEqual(krs.cookCount(), cook_base)


if __name__ == "__main__":
    unittest.main()
