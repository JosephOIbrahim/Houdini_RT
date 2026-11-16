"""Opt-in GUI smoke: launch Houdini with ``waitforui <this file>``.

Requires RT_GUI_SMOKE_ISOLATED=1, RT_TOGGLE_ROOT=<extension>, and
RT_GUI_SMOKE_OUTPUT=<scratch>/result.json.
HOUDINI_USER_PREF_DIR must be inside that scratch directory. No user HIP is
loaded or saved. The test records native renderer selection and pause state;
it does not claim image convergence, pixel correctness or GPU timing.
Set RT_GUI_SMOKE_EXIT=1 to close only this isolated process after the result.
"""

import dataclasses
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

import hou
from hutil.Qt import QtCore, QtWidgets


class PendingReadback(Exception):
    """The native host has not confirmed an asynchronous request yet."""


class ViewerSmoke:
    def __init__(self):
        self.output = Path(os.environ["RT_GUI_SMOKE_OUTPUT"]).resolve()
        # Houdini's command-line Python execution does not define __file__.
        self.extension = Path(os.environ["RT_TOGGLE_ROOT"]).resolve()
        self.records = []
        self.started = time.monotonic()
        self.viewer = None
        self.panel = None
        self.widget = None
        self.previous = None
        self.previous_paused = None
        self.other_viewer = None
        self.steps = [self.setup, self.open_panel, self.enable, self.check_active,
                      self.pause, self.check_paused, self.recreate_panel,
                      self.check_recreated_panel, self.resume, self.check_resumed,
                      self.create_other_viewer, self.check_other_viewer,
                      self.pause, self.check_paused, self.resume,
                      self.check_resumed, self.disable, self.check_restored]
        if os.environ.get("RT_GUI_SMOKE_PREPAUSE") == "1":
            self.steps[1:1] = [self.select_previous_karma, self.pause_previous,
                               self.check_previous_paused]
        self.index = 0
        self.step_deadline = None
        self.result = {
            "status": "running", "houdini": hou.applicationVersionString(),
            "python": sys.version, "pid": os.getpid(),
            "scope": "GUI button dispatch and native renderer/pause readback",
            "image_validation": "not performed", "records": self.records,
            "source_sha256": {name: hashlib.sha256((self.extension / name).read_bytes()).hexdigest()
                for name in ("rt_runtime/render_session.py", "houdini/rt_panel.py",
                             "houdini/rt_panel.pypanel", "verification/gui_viewer_smoke.py")},
        }

    def write(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(self.result, indent=2) + "\n",
                               encoding="utf-8")

    def record(self, step):
        status = self.controller.status(self.viewer)
        record = {
            "step": step, "elapsed_seconds": time.monotonic() - self.started,
            "controller": dataclasses.asdict(status),
            "native_renderer": self.viewer.currentHydraRenderer(),
            "native_paused": self.viewer.isRendererPaused(),
        }
        if self.widget is not None:
            record["panel_labels"] = {
                name: label.text() for name in ("rt_render_state", "rt_render_error")
                if (label := self.widget.findChild(QtWidgets.QLabel, name)) is not None
            }
        if self.other_viewer is not None:
            record["other_viewer"] = {"name": self.other_viewer.name(),
                "renderer": self.other_viewer.currentHydraRenderer(),
                "paused": self.other_viewer.isRendererPaused()}
            assert record["other_viewer"]["renderer"] == self.previous
            assert not record["other_viewer"]["paused"]
        self.records.append(record)
        self.write()
        return status

    def setup(self):
        if os.environ.get("RT_GUI_SMOKE_ISOLATED") != "1":
            raise RuntimeError("Explicit isolated-process opt-in is required")
        prefs = Path(hou.homeHoudiniDirectory()).resolve()
        if not prefs.is_relative_to(self.output.parent):
            raise RuntimeError("Houdini preferences must be inside smoke scratch")
        if not hou.hipFile.isNewFile():
            raise RuntimeError("Refusing to test in a loaded HIP file")
        self.result["preference_directory"] = str(prefs)
        sys.path.insert(0, str(self.extension))
        from rt_runtime.render_session import get_render_controller
        self.controller = get_render_controller()
        viewers = [p for p in hou.ui.curDesktop().paneTabs()
                   if p.type() == hou.paneTabType.SceneViewer]
        if not viewers:
            raise RuntimeError("The isolated desktop has no Scene Viewer")
        self.viewer = viewers[0]
        self.create_test_scene()
        self.viewer.setPwd(hou.node("/stage"))
        self.viewer.setCurrentNode(self.test_node)
        self.viewer.setIsCurrentTab()

    def create_test_scene(self):
        """A fresh supported mesh/dome fixture forces delegate instantiation."""
        from pxr import Gf, Usd, UsdGeom, UsdLux
        scene_path = self.output.parent / "smoke_scene.usda"
        stage = Usd.Stage.CreateNew(str(scene_path))
        mesh = UsdGeom.Mesh.Define(stage, "/quad")
        mesh.CreatePointsAttr([(-2, -2, 0), (2, -2, 0),
                               (2, 2, 0), (-2, 2, 0)])
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.CreateDisplayColorAttr([Gf.Vec3f(0.5, 0.3, 0.1)])
        UsdLux.DomeLight.Define(stage, "/dome").CreateIntensityAttr(1.0)
        stage.GetRootLayer().Save()
        node = hou.node("/stage").createNode("sublayer", "hrt_smoke_scene")
        node.parm("filepath1").set(str(scene_path))
        node.setDisplayFlag(True)
        self.test_node = node
        self.result["fixture"] = str(scene_path)

    def open_panel(self):
        if not self.viewer.isViewingSceneGraph():
            raise RuntimeError("Target viewer did not enter Solaris")
        self.previous = self.viewer.currentHydraRenderer()
        self.previous_paused = self.viewer.isRendererPaused()
        self.result["available_renderers"] = list(self.viewer.hydraRenderers())
        self.result["previous_renderer"] = self.previous
        self.result["previous_paused"] = self.previous_paused
        if self.previous in ("HoudiniRT", "HdRTRendererPlugin"):
            raise RuntimeError("Start with a different renderer to test restoration")
        hou.pypanel.installFile(str(self.extension / "houdini/rt_panel.pypanel"))
        interface = hou.pypanel.interfaceByName("RT_Toggle")
        if interface is None:
            raise RuntimeError("RT_Toggle panel did not register")
        self.panel = hou.ui.curDesktop().createFloatingPaneTab(
            hou.paneTabType.PythonPanel, size=(440, 500),
            python_panel_interface="RT_Toggle", immediate=True)

    def select_previous_karma(self):
        self.viewer.setHydraRenderer("Karma CPU")
        self.viewer.curViewport().draw()

    def pause_previous(self):
        self.viewer.setRendererPaused(True)

    def check_previous_paused(self):
        self.wait_for(self.viewer.isRendererPaused(), "Preparing paused Karma CPU")

    def recreate_panel(self):
        """Destroy the pane widget while paused, then use the same session owner."""
        self.panel.close()
        self.widget = None
        self.panel = hou.ui.curDesktop().createFloatingPaneTab(
            hou.paneTabType.PythonPanel, size=(440, 500),
            python_panel_interface="RT_Toggle", immediate=True)

    def check_recreated_panel(self):
        assert self.button("rt_render_pause").text() == "Resume"
        status = self.record("recreated_panel")
        self.wait_for(status.state == "paused" and not getattr(status, "pending", ""), status)
        assert status.owned and status.previous_renderer == self.previous, status

    def create_other_viewer(self):
        self.other_viewer = hou.ui.curDesktop().createFloatingPaneTab(
            hou.paneTabType.SceneViewer, size=(500, 450), immediate=True)
        self.other_viewer.setPwd(hou.node("/stage"))
        self.other_viewer.setCurrentNode(self.test_node)
        self.other_viewer.setHydraRenderer(self.previous)
        self.other_viewer.setIsCurrentTab()

    def check_other_viewer(self):
        assert self.other_viewer.isViewingSceneGraph()
        assert self.other_viewer != self.viewer
        combo = self.widget.findChild(QtWidgets.QComboBox, "rt_target_viewer")
        assert combo.currentText() == self.viewer.name(), combo.currentText()
        self.record("second_viewer_keeps_original_target")

    def button(self, name):
        if self.widget is None:
            self.widget = self.panel.activeInterfaceRootWidget()
        errors = self.panel.activeInterfaceScriptErrors()
        if errors:
            raise RuntimeError(errors)
        if self.widget is None:
            raise RuntimeError("Panel has no root widget")
        button = self.widget.findChild(QtWidgets.QPushButton, name)
        if button is None or not button.isEnabled():
            raise RuntimeError("Button unavailable: " + name)
        return button

    def enable(self):
        self.button("rt_render_toggle").click()
        self.viewer.curViewport().draw()
        hou.ui.triggerUpdate()
        hou.ui.postRedrawFence()

    def check_active(self):
        status = self.record("enabled")
        self.wait_for(status.state == "active" and not getattr(status, "pending", ""), status)
        assert status.owned, status
        assert self.viewer.currentHydraRenderer() in (
            "HoudiniRT", "HdRTRendererPlugin")
        assert not self.viewer.isRendererPaused()

    def pause(self):
        self.button("rt_render_pause").click()
        self.record("pause_clicked")

    def check_paused(self):
        status = self.record("paused")
        self.wait_for(status.state == "paused" and not getattr(status, "pending", ""), status)
        assert self.viewer.isRendererPaused()
        assert not status.error, status

    def resume(self):
        self.button("rt_render_pause").click()

    def check_resumed(self):
        status = self.record("resumed")
        self.wait_for(status.state == "active" and not getattr(status, "pending", ""), status)
        assert not self.viewer.isRendererPaused()
        assert not status.error, status

    def disable(self):
        self.button("rt_render_toggle").click()

    def check_restored(self):
        status = self.record("restored")
        self.wait_for(status.state == "off" and not status.owned, status)
        assert not status.owned, status
        assert self.viewer.currentHydraRenderer() == self.previous
        assert self.viewer.isRendererPaused() == self.previous_paused

    @staticmethod
    def wait_for(ready, status):
        if not ready:
            raise PendingReadback(str(status))

    def run_next(self):
        if self.index >= len(self.steps):
            self.finish(True)
            return
        step = self.steps[self.index]
        if self.step_deadline is None:
            self.step_deadline = time.monotonic() + 12.0
        print("RT_GUI_SMOKE {:.3f}s {}".format(
            time.monotonic() - self.started, step.__name__),
            file=sys.stderr, flush=True)
        try:
            step()
        except PendingReadback as exc:
            if time.monotonic() < self.step_deadline:
                QtCore.QTimer.singleShot(250, self.run_next)
                return
            self.result["failed_step"] = step.__name__
            self.result["error"] = "Native readback timed out: " + str(exc)
            self.finish(False)
            return
        except Exception:
            self.result["failed_step"] = step.__name__
            self.result["error"] = traceback.format_exc()
            self.finish(False)
            return
        self.index += 1
        self.step_deadline = None
        # Yield to the real Qt/Houdini event loop between actions/readbacks.
        delay_ms = 5000 if step == self.enable else 1000
        QtCore.QTimer.singleShot(delay_ms, self.run_next)

    def finish(self, passed):
        self.result["status"] = "passed" if passed else "failed"
        # Recover only this test's ownership after a failed step.
        if not passed and self.viewer is not None and hasattr(self, "controller"):
            try:
                self.result["recovery"] = dataclasses.asdict(
                    self.controller.deactivate(self.viewer))
            except Exception:
                self.result["recovery_error"] = traceback.format_exc()
        self.write()
        if os.environ.get("RT_GUI_SMOKE_EXIT") == "1":
            def close_isolated_process():
                # The documented native command avoids raising SystemExit
                # through PySide signal dispatch during UI teardown.
                hou.hscript("quit -f {}".format(0 if passed else 1))
            hou.ui.postEventCallback(close_isolated_process)


# Keep the runner alive across asynchronous callbacks for this isolated session.
hou.session._houdinirt_viewer_smoke = ViewerSmoke()
hou.session._houdinirt_viewer_smoke.write()
QtCore.QTimer.singleShot(int(os.environ.get("RT_GUI_SMOKE_START_DELAY_MS", "1000")),
                       hou.session._houdinirt_viewer_smoke.run_next)
