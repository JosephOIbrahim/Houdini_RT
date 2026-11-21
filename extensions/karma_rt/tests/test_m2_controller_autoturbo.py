"""M2 Verification: 13-State KarmaSessionController FSM, Auto-Turbo Lookahead & Fault Injection."""
from __future__ import annotations

import unittest

import hou

from karma_rt.auto_turbo import AutoTurboConfig
from karma_rt.controller import KarmaSessionController, KarmaSessionError, SessionState


class _FakeViewportSettings:
    def __init__(self) -> None:
        self.ray_tracing = False
        self.denoising = False
        self.fast_interactive = False
        self.ortho_grid = True

    def setUseRayTracing(self, v: bool) -> None:
        self.ray_tracing = bool(v)

    def getUseRayTracing(self) -> bool:
        return self.ray_tracing

    def setUseDenoising(self, v: bool) -> None:
        self.denoising = bool(v)

    def getUseDenoising(self) -> bool:
        return self.denoising

    def setFastInteractiveSampling(self, v: bool) -> None:
        self.fast_interactive = bool(v)

    def fastInteractiveSampling(self) -> bool:
        return self.fast_interactive

    def displayOrthoGrid(self) -> bool:
        return self.ortho_grid

    def setDisplayOrthoGrid(self, v: bool) -> None:
        self.ortho_grid = bool(v)


class _FakeGeometryViewport:
    def __init__(self) -> None:
        self._settings = _FakeViewportSettings()
        self._camera_path = "/shot/cam/main"
        self._camera_locked = True
        self._xform = tuple(float(i == j) for i in range(4) for j in range(4))

    def settings(self) -> _FakeViewportSettings:
        return self._settings

    def cameraPath(self) -> str:
        return self._camera_path

    def isCameraLockedToView(self) -> bool:
        return self._camera_locked

    def lockCameraToView(self, lock: bool | None = None) -> bool:
        if lock is not None:
            self._camera_locked = bool(lock)
        return self._camera_locked

    def viewTransform(self) -> tuple[float, ...]:
        return self._xform

    def move_camera(self, tx: float) -> None:
        m = list(self._xform)
        m[12] = float(tx)
        self._xform = tuple(m)

    def resolutionInPixels(self) -> tuple[int, int]:
        return (1280, 720)


class _FakeSolarisSceneViewer:
    """Lightweight SceneViewer test double paired with REAL hou /stage LOPs (never fakes hou in sys.modules)."""

    def __init__(self, name: str, lopnet, *, solaris: bool = True, renderer: str = "Houdini VK") -> None:
        self._name = name
        self._lopnet = lopnet
        self._solaris = solaris
        self._renderer = renderer
        self._paused = False
        self._show_lights = True
        self._show_selection = True
        self._show_render_time = False
        self._show_render_stats = False
        self._use_overrides: dict[str | None, bool] = {}
        self._show_procedurals: dict[str | None, bool] = {}
        self._show_render_purpose: dict[str | None, bool] = {}
        self._stage_serial = 1
        self._stage_locked = False
        self._sg_selection: list[str] = []
        self._callbacks = []
        self._vp = _FakeGeometryViewport()

    def name(self) -> str:
        return self._name

    def type(self):
        return hou.paneTabType.SceneViewer

    def isViewingSceneGraph(self) -> bool:
        return self._solaris

    def pwd(self):
        return self._lopnet

    def hydraRenderers(self) -> tuple[str, ...]:
        return ("Houdini VK", "Karma CPU", "Karma XPU", "Storm")

    def currentHydraRenderer(self) -> str:
        return self._renderer

    def setHydraRenderer(self, name: str) -> None:
        self._renderer = str(name)

    def simulate_native_menu_switch(self, new_renderer: str) -> None:
        self._renderer = new_renderer
        for cb in list(self._callbacks):
            cb(hou.sceneViewerEvent.RendererChanged)

    def isRendererPaused(self) -> bool:
        return self._paused

    def setRendererPaused(self, paused: bool) -> None:
        self._paused = bool(paused)

    def showLights(self) -> bool:
        return self._show_lights

    def setShowLights(self, v: bool) -> None:
        self._show_lights = bool(v)

    def showSelection(self) -> bool:
        return self._show_selection

    def setShowSelection(self, v: bool) -> None:
        self._show_selection = bool(v)

    def showRenderTime(self) -> bool:
        return self._show_render_time

    def setShowRenderTime(self, v: bool) -> None:
        self._show_render_time = bool(v)

    def showRenderStats(self) -> bool:
        return self._show_render_stats

    def setShowRenderStats(self, v: bool) -> None:
        self._show_render_stats = bool(v)

    def usingViewportOverrides(self, renderer: str | None = None) -> bool:
        return self._use_overrides.get(renderer, False)

    def useViewportOverrides(self, v: bool, renderer: str | None = None) -> None:
        self._use_overrides[renderer] = bool(v)

    def showingHydraProcedurals(self, renderer: str | None = None) -> bool:
        return self._show_procedurals.get(renderer, True)

    def showHydraProcedurals(self, v: bool, renderer: str | None = None) -> None:
        self._show_procedurals[renderer] = bool(v)

    def showingRenderPurpose(self, renderer: str | None = None) -> bool:
        return self._show_render_purpose.get(renderer, True)

    def showRenderPurpose(self, v: bool, renderer: str | None = None) -> None:
        self._show_render_purpose[renderer] = bool(v)

    def stageSerial(self) -> int:
        return self._stage_serial

    def sceneGraphStageLocked(self) -> bool:
        return self._stage_locked

    def setSceneGraphStageLocked(self, locked: bool) -> None:
        self._stage_locked = bool(locked)

    def currentSceneGraphSelection(self) -> tuple[str, ...]:
        return tuple(self._sg_selection)

    def curViewport(self) -> _FakeGeometryViewport:
        return self._vp

    def viewports(self) -> tuple[_FakeGeometryViewport, ...]:
        return (self._vp,)

    def addEventCallback(self, cb) -> None:
        if cb not in self._callbacks:
            self._callbacks.append(cb)

    def removeEventCallback(self, cb) -> None:
        if cb in self._callbacks:
            self._callbacks.remove(cb)


class TestM2ControllerAndAutoTurbo(unittest.TestCase):
    def setUp(self) -> None:
        hou.hipFile.clear(suppress_save_prompt=True)
        self.stage_net = hou.node("/stage")
        self.krs = self.stage_net.createNode("karmarendersettings", "krs")
        self.krs.parm("pathtracedsamples").set(128)
        self.krs.setDisplayFlag(True)
        _ = self.krs.stage(apply_viewport_overrides=True)
        self.now = 100.0
        self.ctrl = KarmaSessionController(
            hou_module=hou,
            clock=lambda: self.now,
            auto_turbo_config=AutoTurboConfig(settle_delay_ms=300),
        )

    def test_full_fsm_autoturbo_yield_camera_guard_and_restore(self) -> None:
        viewer = _FakeSolarisSceneViewer("solaris1", self.stage_net, renderer="Houdini VK")

        # Initial observation
        snap0 = self.ctrl.observe(viewer=viewer)
        self.assertEqual(snap0.state, SessionState.READY_OFF)
        self.assertTrue(snap0.camera_locked)

        # Dry-run preview
        prev = self.ctrl.preview_preset("interactive_lookdev", viewer=viewer)
        self.assertTrue(prev.valid)
        self.assertEqual(self.ctrl.engine.list_owned_specs(self.stage_net, self.krs), [])

        # Activate Interactive Lookdev on Karma XPU
        snap1 = self.ctrl.activate_preset("interactive_lookdev", viewer=viewer, delegate_preference="Karma XPU")
        self.assertEqual(snap1.state, SessionState.ACTIVE_IDLE)
        self.assertEqual(snap1.observed_delegate, "Karma XPU")
        self.assertEqual(snap1.previous_renderer, "Houdini VK")
        self.assertTrue(snap1.override_active)
        self.assertTrue(snap1.final_render_untouched)
        self.assertTrue(snap1.clean_overlays_active)
        self.assertFalse(viewer.showLights())
        self.assertFalse(viewer.showSelection())
        self.assertTrue(viewer.usingViewportOverrides("Karma XPU"))

        # Enable Auto-Turbo and simulate camera tumble + quiet settle!
        self.ctrl.set_auto_turbo(True, viewer=viewer)
        self.ctrl.tick_auto_turbo(viewer=viewer, now=self.now)  # prime baseline matrix
        self.now += 0.05
        viewer.curViewport().move_camera(2.5)
        snap_turbo = self.ctrl.tick_auto_turbo(viewer=viewer, now=self.now)
        self.assertEqual(snap_turbo.state, SessionState.ACTIVE_TURBO_INTERACT)
        self.assertTrue(snap_turbo.turbo_engaged)

        # Verify LOP:Custom has ipr_proxydepth == 4 during turbo and production untouched!
        vp_turbo = self.ctrl.engine.read_stage_values(
            self.krs, snap1.settings_prim_path, ["karma:global:ipr_proxydepth", "karma:global:percentofsamples"], True
        )
        self.assertEqual(vp_turbo["karma:global:ipr_proxydepth"], 4)
        self.assertAlmostEqual(vp_turbo["karma:global:percentofsamples"], 25.0, places=3)

        # Regression test: simulate Houdini incrementing stageSerial() right after our own LOP:Custom turbo write!
        # Self-echo suppression must prevent re-triggering Auto-Turbo endlessly so settle completes cleanly.
        viewer._stage_serial += 1
        self.now += 0.06
        self.ctrl.tick_auto_turbo(viewer=viewer, now=self.now)

        # Wait > 300ms quiet -> settles back to ACTIVE_IDLE with lookdev ipr_proxydepth == 2!
        self.now += 0.35
        snap_settled = self.ctrl.tick_auto_turbo(viewer=viewer, now=self.now)
        self.assertEqual(snap_settled.state, SessionState.ACTIVE_IDLE)
        self.assertFalse(snap_settled.turbo_engaged)
        # Also verify post-settle stageSerial echo does NOT re-engage Auto-Turbo!
        viewer._stage_serial += 1
        self.now += 0.06
        snap_post_settle = self.ctrl.tick_auto_turbo(viewer=viewer, now=self.now)
        self.assertEqual(snap_post_settle.state, SessionState.ACTIVE_IDLE)
        self.assertFalse(snap_post_settle.turbo_engaged)
        vp_settled = self.ctrl.engine.read_stage_values(
            self.krs, snap1.settings_prim_path, ["karma:global:ipr_proxydepth"], True
        )
        self.assertEqual(vp_settled["karma:global:ipr_proxydepth"], 2)

        # Shot Camera-Lock Guardrail
        self.assertTrue(viewer.curViewport().lockCameraToView())
        snap_unlocked = self.ctrl.unlock_and_orbit_copy(viewer=viewer)
        self.assertFalse(snap_unlocked.camera_locked)
        self.assertFalse(viewer.curViewport().lockCameraToView())

        # Pause & Resume
        snap_paused = self.ctrl.set_paused(True, viewer=viewer)
        self.assertEqual(snap_paused.state, SessionState.ACTIVE_PAUSED)
        self.assertTrue(snap_paused.paused)
        snap_resumed = self.ctrl.set_paused(False, viewer=viewer)
        self.assertEqual(snap_resumed.state, SessionState.ACTIVE_IDLE)

        # Yield for Disk Render & Resume
        snap_yield = self.ctrl.yield_for_disk_render(viewer=viewer)
        self.assertEqual(snap_yield.state, SessionState.ACTIVE_DISK_RENDER_YIELD)
        self.assertTrue(viewer.isRendererPaused())
        snap_back = self.ctrl.resume_from_disk_render(viewer=viewer)
        self.assertEqual(snap_back.state, SessionState.ACTIVE_IDLE)
        self.assertFalse(viewer.isRendererPaused())

        # Restore Production -> reverts delegate, overlays, and strips ALL owned LOP:Custom specs
        snap_restored = self.ctrl.restore(viewer=viewer)
        self.assertEqual(snap_restored.state, SessionState.READY_OFF)
        self.assertEqual(viewer.currentHydraRenderer(), "Houdini VK")
        self.assertTrue(viewer.showLights())
        self.assertTrue(viewer.showSelection())
        self.assertEqual(self.ctrl.engine.list_owned_specs(self.stage_net, self.krs), [])

    def test_external_takeover_yields_lease_and_cleans_owned_specs(self) -> None:
        viewer = _FakeSolarisSceneViewer("solaris1", self.stage_net, renderer="Houdini VK")
        self.ctrl.activate_preset("live_draft", viewer=viewer, delegate_preference="Karma XPU")
        self.assertGreater(len(self.ctrl.engine.list_owned_specs(self.stage_net, self.krs)), 0)

        # Artist manually switches delegate in Houdini's native viewport menu
        viewer.simulate_native_menu_switch("Storm")
        snap = self.ctrl.observe(viewer=viewer)
        self.assertEqual(snap.observed_delegate, "Storm")
        self.assertEqual(snap.state, SessionState.READY_OFF)
        self.assertFalse(snap.override_active)
        self.assertEqual(
            self.ctrl.engine.list_owned_specs(self.stage_net, self.krs),
            [],
            "External delegate takeover must clean owned LOP:Custom specs without fighting artist!",
        )

    def test_refuses_non_solaris_viewer_and_emergency_reset_cleans_all(self) -> None:
        non_solaris = _FakeSolarisSceneViewer("sop_viewer", self.stage_net, solaris=False)
        with self.assertRaises(KarmaSessionError):
            self.ctrl.resolve_viewer(viewer=non_solaris)

        viewer = _FakeSolarisSceneViewer("solaris1", self.stage_net, renderer="Houdini VK")
        self.ctrl.activate_preset("live_draft", viewer=viewer)
        snap_reset = self.ctrl.emergency_reset(viewer=viewer)
        self.assertEqual(snap_reset.state, SessionState.READY_OFF)
        self.assertEqual(self.ctrl.engine.list_owned_specs(self.stage_net, self.krs), [])

    def test_v2_clay_crop_res_scale_freeze_pin_solo_and_double_denoise_guard(self) -> None:
        # Add geometry + light + H22 imagefilter node to test double-denoise avoidance & solo
        sph = self.stage_net.createNode("sphere", "hero")
        lgt = self.stage_net.createNode("light::2.0", "key_lgt")
        mrg = self.stage_net.createNode("merge", "merge_v2")
        mrg.setInput(0, sph)
        mrg.setInput(1, lgt)
        self.krs.setInput(0, mrg)
        _ = self.krs.stage(apply_viewport_overrides=True)

        viewer = _FakeSolarisSceneViewer("solaris1", self.stage_net, renderer="Houdini VK")
        viewer._sg_selection = ["/hero"]
        orig_follow = hou.lop.panesFollowCurrentNode()
        try:
            # 1. Clay / Headlight AO preset
            snap_clay = self.ctrl.activate_preset("clay_headlight", viewer=viewer, delegate_preference="Karma XPU")
            self.assertEqual(snap_clay.active_preset_id, "clay_headlight")
            self.assertNotEqual(snap_clay.xpu_device_summary, "UNKNOWN")
            vals = self.ctrl.engine.read_stage_values(
                self.krs, snap_clay.settings_prim_path, ["karma:global:override_lighting", "karma:global:head_ao_samples"], True
            )
            self.assertEqual(vals["karma:global:override_lighting"], "Headlight")
            self.assertEqual(vals["karma:global:head_ao_samples"], 4)

            # 2. Focus Crop + Resolution Scale compose cleanly and survive Auto-Turbo settle!
            snap_crop = self.ctrl.set_focus_crop("center_50", viewer=viewer)
            self.assertEqual(snap_crop.focus_crop, "center_50")
            snap_res = self.ctrl.set_resolution_scale(0.5, viewer=viewer)
            self.assertAlmostEqual(snap_res.resolution_scale, 0.5, places=3)
            mod_vals = self.ctrl.engine.read_stage_values(
                self.krs, snap_clay.settings_prim_path, ["dataWindowNDC", "resolution"], True
            )
            self.assertEqual(mod_vals["dataWindowNDC"], (0.25, 0.25, 0.75, 0.75))
            self.assertEqual(mod_vals["resolution"], (640, 360))

            # 3. Freeze VP, Pin Stage, Solo Geo, & Restore cleans everything back!
            snap_frz = self.ctrl.set_viewport_frozen(True, viewer=viewer)
            self.assertTrue(snap_frz.viewport_frozen)
            self.assertTrue(viewer.sceneGraphStageLocked())

            snap_pin = self.ctrl.set_stage_pinned(True, viewer=viewer)
            self.assertTrue(snap_pin.stage_pinned)
            self.assertFalse(hou.lop.panesFollowCurrentNode())

            snap_solo = self.ctrl.solo_selected_geometry(viewer=viewer)
            self.assertEqual(snap_solo.solo_geometry_count, 1)

            snap_restored = self.ctrl.restore(viewer=viewer)
            self.assertEqual(snap_restored.state, SessionState.READY_OFF)
            self.assertFalse(viewer.sceneGraphStageLocked())
            self.assertEqual(hou.lop.panesFollowCurrentNode(), orig_follow)
            self.assertEqual(snap_restored.solo_geometry_count, 0)
            self.assertEqual(snap_restored.focus_crop, "full")
            self.assertAlmostEqual(snap_restored.resolution_scale, 1.0, places=3)

            # 4. Double-denoiser protection when USD HoudiniImageFilterList exists on stage
            py_flt = self.stage_net.createNode("pythonscript", "add_img_filter")
            py_flt.setInput(0, self.krs)
            py_flt.parm("python").set(
                "st = hou.pwd().editableStage()\n"
                "st.DefinePrim('/Render/Products/ImageFilters', 'HoudiniImageFilterList')\n"
            )
            py_flt.setDisplayFlag(True)
            _ = py_flt.stage(apply_viewport_overrides=True)

            snap_dd = self.ctrl.activate_preset("live_draft", viewer=viewer, delegate_preference="Karma XPU")
            self.assertTrue(snap_dd.usd_image_filters_detected)
            self.assertEqual(snap_dd.denoiser_mode, "usd_image_filter")
            self.assertNotIn("karma:global:imagefilter", snap_dd.last_receipt.written_attrs)
            self.ctrl.restore(viewer=viewer)
        finally:
            hou.lop.setPanesFollowCurrentNode(orig_follow)


if __name__ == "__main__":
    unittest.main()
