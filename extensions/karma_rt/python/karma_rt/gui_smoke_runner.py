"""Automated non-blocking QTimer-staged live Houdini 22 GUI smoke verifier for KarmaRT.

Executes the full artist Solaris/Karma XPU workflow inside a live Houdini 22 GUI
session without freezing Qt, records structured JSON evidence + panel/window
screenshots, and verifies all non-destructive LOP:Custom invariants live.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import traceback
from typing import Any

_SCHEDULED = False
_ACTIVE_RUNNER: Any = None
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _default_evidence_dir() -> Path:
    custom = os.environ.get("KARMA_RT_EVIDENCE_DIR", "").strip()
    if custom:
        return Path(custom)
    return _PACKAGE_ROOT / "demo" / "smoke_evidence"


def _default_report_path() -> Path:
    custom = os.environ.get("KARMA_RT_REPORT_PATH", "").strip()
    if custom:
        return Path(custom)
    return _default_evidence_dir() / "gui_smoke_report.json"


def _default_demo_scene() -> Path:
    custom = os.environ.get("KARMA_RT_DEMO_SCENE", "").strip()
    if custom:
        return Path(custom)
    return _PACKAGE_ROOT / "demo" / "karmart_solaris_smoke.hiplc"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _grab_screenshot(widget: Any, dest: Path) -> str:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if widget is not None and hasattr(widget, "grab"):
            pix = widget.grab()
            if pix is not None and not pix.isNull() and pix.save(str(dest)):
                return dest.as_posix()
    except Exception:
        pass
    return ""


def _grab_main_window_screenshot(dest: Path) -> str:
    try:
        import hou
        from PySide6 import QtWidgets

        dest.parent.mkdir(parents=True, exist_ok=True)
        main_win = hou.qt.mainWindow()
        screen = QtWidgets.QApplication.primaryScreen()
        if main_win is not None and screen is not None:
            geo = main_win.frameGeometry()
            pix = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
            if pix is not None and not pix.isNull() and pix.save(str(dest)):
                return dest.as_posix()
        return _grab_screenshot(main_win, dest)
    except Exception:
        return ""


class LiveGuiSmokeRunner:
    """QTimer-paced live GUI verification runner inside Houdini 22."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.evidence_dir = _default_evidence_dir()
        self.report_path = _default_report_path()
        self.demo_scene = _default_demo_scene()
        self.exit_on_done = os.environ.get("KARMA_RT_EXIT_ON_DONE", "0").strip() == "1"
        self.final_preset = os.environ.get("KARMA_RT_FINAL_PRESET", "interactive_lookdev").strip()

        self.started_at = time.time()
        self.checks: list[dict[str, Any]] = []
        self.screenshots: dict[str, str] = {}
        self.metadata: dict[str, Any] = {"mode": mode}

        self.viewer: Any = None
        self.panel_tab: Any = None
        self.panel_widget: Any = None
        self.krs_node: Any = None
        self.stage_net: Any = None
        self.ctrl: Any = None
        self.initial_cook: int = 0
        self.initial_children: int = 0
        self.initial_renderer: str = ""
        self.settings_prim_path: str = ""

        self._step_idx = 0
        self._steps: list[tuple[str, int, Any]] = []
        self._timer: Any = None

    def _record(self, name: str, passed: bool, detail: str, data: dict[str, Any] | None = None) -> None:
        self.checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "detail": str(detail),
                "data": data or {},
            }
        )

    def start(self) -> None:
        from PySide6 import QtCore

        if self.mode == "open_demo":
            self._steps = [
                ("load_scene_and_solaris", 500, self._step_load_scene_and_solaris),
                ("open_panel_and_ready", 800, self._step_open_panel_only),
            ]
        else:
            self._steps = [
                ("load_scene_and_solaris", 600, self._step_load_scene_and_solaris),
                ("open_panel_and_preflight", 1000, self._step_open_panel_and_preflight),
                ("preview_diff_dry_run", 400, self._step_preview_diff),
                ("activate_live_draft", 600, self._step_activate_live_draft),
                ("verify_live_draft_xpu", 2600, self._step_verify_live_draft),
                ("switch_interactive_lookdev", 600, self._step_switch_lookdev),
                ("verify_lookdev_and_engage_turbo", 2400, self._step_verify_lookdev_and_turbo),
                ("verify_turbo_settle_and_advisor", 800, self._step_verify_settle_and_advisor),
                ("restore_and_verify_purity", 1000, self._step_restore_and_verify),
                ("finalize_session", 1200, self._step_finalize),
            ]

        self._timer = QtCore.QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._run_next_step)
        self._schedule_next()

    def _schedule_next(self) -> None:
        if self._step_idx >= len(self._steps):
            return
        _name, delay_ms, _fn = self._steps[self._step_idx]
        self._timer.start(delay_ms)

    def _run_next_step(self) -> None:
        if self._step_idx >= len(self._steps):
            return
        name, _delay, fn = self._steps[self._step_idx]
        self._step_idx += 1
        try:
            fn()
        except Exception as exc:
            self._record(
                name,
                False,
                f"Unhandled exception: {exc}",
                {"traceback": traceback.format_exc()},
            )
            self._finish_with_report(aborted=True)
            return

        if self._step_idx < len(self._steps):
            self._schedule_next()

    # ------------------------------------------------------------------ Steps

    def _step_load_scene_and_solaris(self) -> None:
        import hou
        from karma_rt.controller import get_controller

        self.metadata["houdini_version"] = hou.applicationVersionString()
        self.metadata["license"] = str(hou.licenseCategory())

        # Ensure demo scene is loaded
        current_hip = Path(hou.hipFile.path()).resolve() if hou.hipFile.path() else None
        if self.demo_scene.exists() and (current_hip is None or current_hip != self.demo_scene.resolve()):
            hou.hipFile.load(self.demo_scene.as_posix(), suppress_save_prompt=True)

        # Switch desktop to Solaris
        desktop = hou.ui.desktop("Solaris")
        if desktop is not None:
            desktop.setAsCurrent()

        self.stage_net = hou.node("/stage")
        self.krs_node = (
            self.stage_net.node("production_karma_settings")
            if self.stage_net is not None
            else None
        )
        if self.krs_node is None and self.stage_net is not None:
            self.krs_node = self.stage_net.displayNode()

        if self.krs_node is not None:
            self.krs_node.setDisplayFlag(True)
            self.krs_node.setCurrent(True, clear_all_selected=True)

        self.ctrl = get_controller()
        viewers = self.ctrl.list_solaris_viewers()
        if not viewers:
            # Force SceneViewer pwd to /stage if Solaris desktop just switched
            for pane in hou.ui.paneTabs():
                if pane.type() == hou.paneTabType.SceneViewer and self.stage_net is not None:
                    pane.setPwd(self.stage_net)
                    break
            viewers = self.ctrl.list_solaris_viewers()

        self.viewer = viewers[0] if viewers else None
        if self.viewer is not None:
            try:
                self.viewer.curViewport().setCamera("/shot/cam/main")
            except Exception:
                pass
            self.initial_renderer = str(self.viewer.currentHydraRenderer())

        if self.krs_node is not None:
            _ = self.krs_node.stage(apply_viewport_overrides=True)
            self.initial_cook = int(self.krs_node.cookCount())
            self.initial_children = len(self.stage_net.children())

        ok = self.viewer is not None and self.krs_node is not None
        self._record(
            "load_scene_and_solaris",
            ok,
            (
                f"Solaris viewer='{self.viewer.name() if self.viewer else 'NONE'}', "
                f"initial_renderer='{self.initial_renderer}', krs='{self.krs_node.path() if self.krs_node else 'NONE'}', "
                f"baseline_cook={self.initial_cook}"
            ),
            {
                "viewer": self.viewer.name() if self.viewer else "",
                "initial_renderer": self.initial_renderer,
                "baseline_cook": self.initial_cook,
                "stage_children": self.initial_children,
            },
        )

    def _step_open_panel_only(self) -> None:
        from karma_rt.ui.shelf_actions import open_panel

        self.panel_tab = open_panel()
        if self.panel_tab is not None and hasattr(self.panel_tab, "activeInterfaceRootWidget"):
            self.panel_widget = self.panel_tab.activeInterfaceRootWidget()
        if self.viewer is not None and self.final_preset:
            self.ctrl.activate_preset(
                self.final_preset,
                viewer=self.viewer,
                delegate_preference="Karma XPU",
            )
            self.ctrl.set_auto_turbo(True, viewer=self.viewer)
        self._record(
            "open_panel_and_ready",
            self.panel_widget is not None,
            f"Opened Karma RT panel and activated '{self.final_preset}' with Auto-Turbo ON.",
        )
        self._finish_with_report(aborted=False)

    def _step_open_panel_and_preflight(self) -> None:
        from karma_rt.ui.panel import KarmaRTPanelWidget
        from karma_rt.ui.shelf_actions import open_panel

        self.panel_tab = open_panel()
        if self.panel_tab is not None and hasattr(self.panel_tab, "activeInterfaceRootWidget"):
            self.panel_widget = self.panel_tab.activeInterfaceRootWidget()

        panel_ok = isinstance(self.panel_widget, KarmaRTPanelWidget)
        report = (
            self.panel_widget.run_preflight_doctor()
            if panel_ok
            else self.ctrl.run_preflight(viewer=self.viewer)
        )
        ok = (
            panel_ok
            and report.status in ("READY", "DEGRADED")
            and report.karma_xpu_available
            and report.karma_cpu_available
            and report.vulkan_available
        )
        self._record(
            "open_panel_and_preflight",
            ok,
            f"Panel={panel_ok}, Doctor={report.status}, H={report.houdini_version}, delegates={list(report.available_delegates)}",
            report.to_dict(),
        )

    def _step_preview_diff(self) -> None:
        preview = self.ctrl.preview_preset("live_draft", viewer=self.viewer)
        restart_diffs = [d.usd_attr for d in preview.diffs if d.triggers_restart]
        cook_now = int(self.krs_node.cookCount())
        ok = (
            preview.valid
            and len(restart_diffs) == 0
            and len(preview.diffs) >= 8
            and cook_now == self.initial_cook
        )
        self._record(
            "preview_diff_dry_run",
            ok,
            f"valid={preview.valid}, diffs={len(preview.diffs)}, restart_attrs={restart_diffs}, cook_delta={cook_now - self.initial_cook}",
            {
                "settings_prim": preview.binding.settings_prim_path,
                "diff_count": len(preview.diffs),
                "restart_attrs": restart_diffs,
                "cook_delta": cook_now - self.initial_cook,
            },
        )

    def _step_activate_live_draft(self) -> None:
        snap = self.ctrl.activate_preset(
            "live_draft",
            viewer=self.viewer,
            delegate_preference="Karma XPU",
        )
        self.settings_prim_path = snap.settings_prim_path
        receipt = snap.last_receipt
        apply_ms = getattr(receipt, "apply_ms", None)
        self._record(
            "activate_live_draft",
            snap.override_active and snap.observed_delegate == "Karma XPU",
            f"state={snap.state}, delegate={snap.observed_delegate}, apply_ms={apply_ms:.2f}ms"
            if isinstance(apply_ms, (int, float))
            else f"state={snap.state}, delegate={snap.observed_delegate}",
            {
                "state": snap.state,
                "observed_delegate": snap.observed_delegate,
                "settings_prim_path": snap.settings_prim_path,
                "apply_ms": apply_ms,
            },
        )

    def _step_verify_live_draft(self) -> None:
        snap = self.ctrl.observe(viewer=self.viewer)
        vp_vals = self.ctrl.engine.read_stage_values(
            self.krs_node,
            self.settings_prim_path,
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
        prod_vals = self.ctrl.engine.read_stage_values(
            self.krs_node,
            self.settings_prim_path,
            ["karma:global:pathtracedsamples", "karma:global:ipr_proxydepth", "resolution"],
            apply_viewport_overrides=False,
        )
        krs_parm_samples = int(self.krs_node.parm("pathtracedsamples").eval())
        cook_delta = int(self.krs_node.cookCount()) - self.initial_cook
        children_delta = len(self.stage_net.children()) - self.initial_children
        using_ov = bool(self.viewer.usingViewportOverrides("Karma XPU"))
        clean_ov = (not bool(self.viewer.showLights())) and (not bool(self.viewer.showSelection()))
        badge_visible = bool(
            self.panel_widget is not None
            and hasattr(self.panel_widget, "override_badge")
            and not self.panel_widget.override_badge.isHidden()
        )
        gpu_badge_ok = bool(snap.xpu_device_summary and snap.xpu_device_summary != "UNKNOWN")

        # Capture real screenshots of Live Draft in Karma XPU!
        p_shot = _grab_screenshot(self.panel_widget, self.evidence_dir / "panel_live_draft.png")
        w_shot = _grab_main_window_screenshot(self.evidence_dir / "houdini_live_draft.png")
        if p_shot:
            self.screenshots["panel_live_draft"] = p_shot
        if w_shot:
            self.screenshots["houdini_live_draft"] = w_shot

        ok = (
            snap.observed_delegate == "Karma XPU"
            and vp_vals["karma:global:pathtracedsamples"] == 9
            and vp_vals["karma:global:ipr_proxydepth"] == 3
            and vp_vals["karma:global:colorlimitindlinked"] is False
            and vp_vals["karma:global:convergence_mode"] == "Path Traced"
            and vp_vals["instantaneousShutter"] is True
            and prod_vals["karma:global:pathtracedsamples"] == 128
            and prod_vals["karma:global:ipr_proxydepth"] is None
            and krs_parm_samples == 128
            and cook_delta == 0
            and children_delta == 0
            and using_ov
            and clean_ov
            and badge_visible
            and gpu_badge_ok
        )
        self._record(
            "verify_live_draft_xpu",
            ok,
            (
                f"delegate={snap.observed_delegate}, vp_spp={vp_vals['karma:global:pathtracedsamples']}, "
                f"vp_proxy={vp_vals['karma:global:ipr_proxydepth']}, indlinked={vp_vals['karma:global:colorlimitindlinked']}, "
                f"conv={vp_vals['karma:global:convergence_mode']}, prod_spp={prod_vals['karma:global:pathtracedsamples']}, "
                f"gpu='{snap.xpu_device_summary}', cook_delta={cook_delta}, children_delta={children_delta}"
            ),
            {
                "vp_vals": vp_vals,
                "prod_vals": prod_vals,
                "krs_parm_samples": krs_parm_samples,
                "cook_delta": cook_delta,
                "children_delta": children_delta,
                "using_viewport_overrides": using_ov,
                "clean_overlays": clean_ov,
                "badge_visible": badge_visible,
                "gpu_summary": snap.xpu_device_summary,
            },
        )

    def _step_switch_lookdev(self) -> None:
        # Exercise v2.0 Clay / Headlight AO, Focus Crop, Res Scale, Freeze VP, & Pin Stage first, then switch to Interactive Lookdev!
        snap_clay = self.ctrl.activate_preset(
            "clay_headlight",
            viewer=self.viewer,
            delegate_preference="Karma XPU",
        )
        clay_vals = self.ctrl.engine.read_stage_values(
            self.krs_node,
            self.settings_prim_path,
            ["karma:global:override_lighting", "karma:global:head_ao_samples"],
            apply_viewport_overrides=True,
        )
        snap_crop = self.ctrl.set_focus_crop("center_50", viewer=self.viewer)
        snap_res = self.ctrl.set_resolution_scale(0.5, viewer=self.viewer)
        mod_vals = self.ctrl.engine.read_stage_values(
            self.krs_node,
            self.settings_prim_path,
            ["dataWindowNDC", "resolution"],
            apply_viewport_overrides=True,
        )
        prod_res = self.ctrl.engine.read_production_resolution(self.krs_node, self.settings_prim_path)
        self.ctrl.set_viewport_frozen(True, viewer=self.viewer)
        frozen_on = bool(self.viewer.sceneGraphStageLocked())
        self.ctrl.set_viewport_frozen(False, viewer=self.viewer)
        self.ctrl.set_stage_pinned(True, viewer=self.viewer)
        self.ctrl.set_stage_pinned(False, viewer=self.viewer)

        # Reset crop/scale and switch to Interactive Lookdev (36 spp)
        self.ctrl.set_focus_crop("full", viewer=self.viewer)
        self.ctrl.set_resolution_scale(1.0, viewer=self.viewer)
        snap = self.ctrl.activate_preset(
            "interactive_lookdev",
            viewer=self.viewer,
            delegate_preference="Karma XPU",
        )
        cook_delta = int(self.krs_node.cookCount()) - self.initial_cook
        vp_vals = self.ctrl.engine.read_stage_values(
            self.krs_node,
            self.settings_prim_path,
            ["karma:global:pathtracedsamples", "karma:global:ipr_proxydepth", "karma:global:light_sampling_mode"],
            apply_viewport_overrides=True,
        )
        ok = (
            snap_clay.active_preset_id == "clay_headlight"
            and clay_vals["karma:global:override_lighting"] == "Headlight"
            and clay_vals["karma:global:head_ao_samples"] == 4
            and snap_crop.focus_crop == "center_50"
            and abs(snap_res.resolution_scale - 0.5) < 1e-3
            and mod_vals["dataWindowNDC"] == (0.25, 0.25, 0.75, 0.75)
            and mod_vals["resolution"] == (640, 360)
            and prod_res == (1280, 720)
            and frozen_on
            and snap.active_preset_id == "interactive_lookdev"
            and vp_vals["karma:global:pathtracedsamples"] == 36
            and vp_vals["karma:global:ipr_proxydepth"] == 2
            and vp_vals["karma:global:light_sampling_mode"] == "Light Tree"
            and cook_delta == 0
        )
        self._record(
            "switch_interactive_lookdev",
            ok,
            (
                f"clay={clay_vals['karma:global:override_lighting']}, crop={mod_vals['dataWindowNDC']}, "
                f"scaled_res={mod_vals['resolution']} (prod={prod_res}), frozen_ok={frozen_on}, "
                f"lookdev_spp={vp_vals['karma:global:pathtracedsamples']}, cook_delta={cook_delta}"
            ),
            {
                "clay_vals": clay_vals,
                "mod_vals": mod_vals,
                "prod_res": prod_res,
                "vp_vals": vp_vals,
                "cook_delta": cook_delta,
            },
        )

    def _step_verify_lookdev_and_turbo(self) -> None:
        import hou
        from karma_rt.controller import SessionState

        # Screenshot settled Interactive Lookdev
        w_shot = _grab_main_window_screenshot(self.evidence_dir / "houdini_interactive_lookdev.png")
        if w_shot:
            self.screenshots["houdini_interactive_lookdev"] = w_shot

        # Enable Auto-Turbo, prime baseline, nudge viewport camera, and tick!
        self.ctrl.set_auto_turbo(True, viewer=self.viewer)
        self.ctrl.tick_auto_turbo(viewer=self.viewer)

        vp = self.viewer.curViewport()
        # Switch to free default camera or tumble safely so /shot/cam/main is never mutated!
        try:
            vp.lockCameraToView(False)
            vp.useDefaultCamera()
            dc = vp.defaultCamera()
            pivot = dc.pivot()
            dc.setTranslation(hou.Vector3(pivot[0] + 0.65, pivot[1] + 0.25, pivot[2] + 0.45))
            dc.setRotation(hou.Matrix3(((0.98, 0.0, 0.19), (0.0, 1.0, 0.0), (-0.19, 0.0, 0.98))))
        except Exception:
            # Fallback: notify lookahead interaction impulse if camera manipulation unavailable
            self.ctrl.auto_turbo.notify_interaction_event(self.viewer.name(), "Resizing")

        snap_turbo = self.ctrl.tick_auto_turbo(viewer=self.viewer)
        vp_turbo = self.ctrl.engine.read_stage_values(
            self.krs_node,
            self.settings_prim_path,
            ["karma:global:ipr_proxydepth", "karma:global:percentofsamples"],
            apply_viewport_overrides=True,
        )
        cook_delta = int(self.krs_node.cookCount()) - self.initial_cook
        ok = (
            snap_turbo.state == SessionState.ACTIVE_TURBO_INTERACT
            and snap_turbo.turbo_engaged
            and vp_turbo["karma:global:ipr_proxydepth"] == 4
            and abs(float(vp_turbo["karma:global:percentofsamples"] or 0.0) - 25.0) < 1e-3
            and cook_delta == 0
        )
        self._record(
            "auto_turbo_engage_on_tumble",
            ok,
            f"state={snap_turbo.state}, turbo_engaged={snap_turbo.turbo_engaged}, proxy={vp_turbo['karma:global:ipr_proxydepth']}, pct={vp_turbo['karma:global:percentofsamples']}, cook_delta={cook_delta}",
            {"vp_turbo": vp_turbo, "cook_delta": cook_delta},
        )

    def _step_verify_settle_and_advisor(self) -> None:
        from karma_rt.controller import SessionState

        # After 800ms quiet dwell (> 300ms settle_delay_ms), Auto-Turbo must settle back to ACTIVE_IDLE!
        snap_settled = self.ctrl.tick_auto_turbo(viewer=self.viewer)
        vp_settled = self.ctrl.engine.read_stage_values(
            self.krs_node,
            self.settings_prim_path,
            ["karma:global:ipr_proxydepth"],
            apply_viewport_overrides=True,
        )
        cook_delta_settle = int(self.krs_node.cookCount()) - self.initial_cook
        settle_ok = (
            snap_settled.state == SessionState.ACTIVE_IDLE
            and not snap_settled.turbo_engaged
            and vp_settled["karma:global:ipr_proxydepth"] == 2
            and cook_delta_settle == 0
        )
        self._record(
            "auto_turbo_settle_hysteresis",
            settle_ok,
            f"state={snap_settled.state}, turbo_engaged={snap_settled.turbo_engaged}, proxy={vp_settled['karma:global:ipr_proxydepth']}, cook_delta={cook_delta_settle}",
            {"vp_settled": vp_settled, "cook_delta": cook_delta_settle},
        )

        # Run Read-Only Scene Bottleneck Advisor
        telemetry, advisories = self.ctrl.scan_advisories(viewer=self.viewer)
        cook_delta_adv = int(self.krs_node.cookCount()) - self.initial_cook
        self._record(
            "scene_bottleneck_advisor",
            cook_delta_adv == 0 and telemetry.mesh_count >= 2 and telemetry.light_count >= 1,
            f"prims={telemetry.prim_count}, meshes={telemetry.mesh_count}, lights={telemetry.light_count}, scan_ms={telemetry.scan_ms:.2f}, advisories={[a.id for a in advisories]}, cook_delta={cook_delta_adv}",
            {
                "telemetry": telemetry.to_dict(),
                "advisory_ids": [a.id for a in advisories],
                "cook_delta": cook_delta_adv,
            },
        )

    def _step_restore_and_verify(self) -> None:
        import hou
        from karma_rt.controller import SessionState

        # Also test BeforeSave/AfterSave HIP purity while overrides were active -> first save while active!
        saved_hip = (self.evidence_dir / "smoke_saved_clean.hiplc").as_posix()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        hou.hipFile.save(saved_hip)
        owned_after_save = self.ctrl.engine.list_owned_specs(self.stage_net, display_lop=self.krs_node)
        cook_before_restore = int(self.krs_node.cookCount())

        # Now Restore Production!
        snap_restored = self.ctrl.restore(viewer=self.viewer)
        owned_after_restore = self.ctrl.engine.list_owned_specs(self.stage_net, display_lop=self.krs_node)
        cook_delta = int(self.krs_node.cookCount()) - cook_before_restore
        prod_spp = int(self.krs_node.parm("pathtracedsamples").eval())

        restored_ok = (
            snap_restored.state == SessionState.READY_OFF
            and snap_restored.observed_delegate == self.initial_renderer
            and len(owned_after_save) > 0
            and owned_after_restore == []
            and bool(self.viewer.showLights())
            and bool(self.viewer.showSelection())
            and prod_spp == 128
            and cook_delta == 0
        )
        self._record(
            "restore_production_and_hip_save_hook",
            restored_ok,
            (
                f"state={snap_restored.state}, delegate={snap_restored.observed_delegate} (expected {self.initial_renderer}), "
                f"owned_after_save={len(owned_after_save)}, owned_after_restore={len(owned_after_restore)}, "
                f"prod_spp={prod_spp}, cook_delta={cook_delta}"
            ),
            {
                "saved_hip": saved_hip,
                "owned_after_save": len(owned_after_save),
                "owned_after_restore": owned_after_restore,
                "prod_spp": prod_spp,
                "cook_delta": cook_delta,
            },
        )

    def _step_finalize(self) -> None:
        # If keeping Houdini open for the artist, leave it in clean Interactive Lookdev on /shot/cam/main!
        if not self.exit_on_done and self.final_preset and self.viewer is not None:
            try:
                self.viewer.curViewport().setCamera("/shot/cam/main")
            except Exception:
                pass
            self.ctrl.activate_preset(
                self.final_preset,
                viewer=self.viewer,
                delegate_preference="Karma XPU",
            )
            self.ctrl.set_auto_turbo(True, viewer=self.viewer)
        self._finish_with_report(aborted=False)

    def _finish_with_report(self, *, aborted: bool) -> None:
        passed_count = sum(1 for c in self.checks if c["passed"])
        total_count = len(self.checks)
        all_ok = (not aborted) and total_count > 0 and passed_count == total_count
        ledger_stats = self.ctrl.ledger.summary_stats() if self.ctrl is not None else {}

        report = {
            "ok": all_ok,
            "aborted": aborted,
            "passed": passed_count,
            "total": total_count,
            "elapsed_sec": round(time.time() - self.started_at, 2),
            "metadata": self.metadata,
            "screenshots": self.screenshots,
            "ledger_stats": ledger_stats,
            "checks": self.checks,
        }
        _write_json_atomic(self.report_path, report)

        try:
            import hou

            sev = hou.severityType.Message if all_ok else hou.severityType.Warning
            hou.ui.setStatusMessage(
                f"Karma RT Live GUI Smoke: {passed_count}/{total_count} PASSED ({report['elapsed_sec']}s)",
                severity=sev,
            )
        except Exception:
            pass

        if self.exit_on_done:
            from PySide6 import QtCore

            def _do_exit() -> None:
                try:
                    import hou

                    hou.exit(0 if all_ok else 1, suppress_save_prompt=True)
                except Exception:
                    os._exit(0 if all_ok else 1)

            QtCore.QTimer.singleShot(400, _do_exit)


def schedule_autorun(mode: str = "gui_smoke") -> None:
    """Schedule the live GUI smoke/demo runner once UI initialization finishes."""
    global _SCHEDULED, _ACTIVE_RUNNER
    if _SCHEDULED:
        return
    _SCHEDULED = True

    def _boot() -> None:
        global _ACTIVE_RUNNER
        runner = LiveGuiSmokeRunner(mode=mode)
        _ACTIVE_RUNNER = runner
        runner.start()

    try:
        import hdefereval

        hdefereval.executeDeferred(_boot)
    except Exception:
        _boot()
