"""Opt-in verification of normal Houdini packages in a fresh test process.

No sys.path edits, plugin registration or panel installation occurs here.
RT_INSTALLED_VERIFY=1, RT_INSTALLED_MANIFEST and RT_INSTALLED_OUTPUT are required.
The script creates only a fresh scratch scene, uses discovered installed controls,
and exits this process through native hscript when RT_INSTALLED_EXIT=1.
"""
import ctypes
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


class Pending(Exception):
    pass


class InstalledSmoke:
    def __init__(self):
        self.output = Path(os.environ["RT_INSTALLED_OUTPUT"]).resolve()
        self.manifest_path = Path(os.environ["RT_INSTALLED_MANIFEST"]).resolve()
        self.payload = self.manifest_path.parent
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8-sig"))
        self.started = time.monotonic()
        self.viewer = self.other = self.panel = self.widget = None
        self.safe_to_exit = False
        self.index = 0
        self.deadline = None
        self.records = []
        self.result = {"status": "running", "pid": os.getpid(),
            "houdini": hou.applicationVersionString(), "records": self.records,
            "scope": "Normal installed package discovery, Qt buttons and native renderer/pause readback",
            "image_validation": "not performed", "installed_files_verified": {},
            "manifest": str(self.manifest_path),
            "manifest_sha256": self.sha(self.manifest_path)}
        self.steps = [self.setup, self.select_karma, self.pause_karma, self.check_karma,
            self.open_panel, self.target_viewer, self.enable, self.check_active,
            self.pause, self.check_paused, self.recreate_panel, self.target_viewer,
            self.check_recreated, self.resume, self.check_active,
            self.create_other, self.check_other, self.pause, self.check_paused,
            self.resume, self.check_active, self.disable, self.check_restored]

    @staticmethod
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def write(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(self.result, indent=2) + "\n", encoding="utf-8")

    def verify_file(self, path):
        path = Path(path).resolve()
        name = path.relative_to(self.payload).as_posix()
        entry = self.manifest["files"][name]
        expected = entry["sha256"] if isinstance(entry, dict) else entry
        actual = self.sha(path)
        assert actual == expected, (str(path), expected, actual)
        self.result["installed_files_verified"][name] = {"path": str(path), "sha256": actual}
        return str(path)

    def setup(self):
        assert os.environ.get("RT_INSTALLED_VERIFY") == "1", "Explicit test opt-in required"
        assert hou.hipFile.isNewFile(), "Refusing to test a loaded user HIP"
        assert hou.applicationVersionString() == self.manifest["houdini_build"]
        prefs = Path(hou.homeHoudiniDirectory()).resolve()
        assert prefs == Path("C:/Users/User/OneDrive/Documents/houdini22.0").resolve(), prefs
        assert prefs == Path(self.manifest["preferences_dir"]).resolve(), prefs
        assert self.payload == Path(self.manifest["installation_root"]).resolve()
        package = Path(self.manifest["package_path"])
        assert self.sha(package) == self.manifest["package_sha256"], str(package)
        self.safe_to_exit = True
        self.result["installed_package"] = str(package)
        self.result["normal_preferences"] = str(prefs)
        import rt_runtime.render_session as render_session
        self.result["imported_controller"] = self.verify_file(render_session.__file__)
        self.controller = render_session.get_render_controller()
        interface = hou.pypanel.interfaceByName("RT_Toggle")
        assert interface is not None, "Installed RT_Toggle interface was not discovered"
        self.result["discovered_panel"] = self.verify_file(interface.filePath())
        self.result["effective_paths"] = {name: hou.getenv(name) for name in
            ("HOUDINI_PATH", "PXR_PLUGINPATH_NAME", "RT_TOGGLE_ROOT")}
        viewers = [p for p in hou.ui.curDesktop().paneTabs()
                   if p.type() == hou.paneTabType.SceneViewer]
        assert viewers, "Fresh desktop has no Scene Viewer"
        self.viewer = viewers[0]
        self.create_scene()
        self.viewer.setPwd(hou.node("/stage"))
        self.viewer.setCurrentNode(self.node)
        self.viewer.setIsCurrentTab()
        self.result["intended_viewer"] = self.viewer.name()

    @staticmethod
    def ready(condition, detail):
        if not condition:
            raise Pending(str(detail))

    def create_scene(self):
        from pxr import Gf, Usd, UsdGeom, UsdLux
        scene_path = self.output.parent / "installed_smoke_scene.usda"
        stage = Usd.Stage.CreateNew(str(scene_path))
        mesh = UsdGeom.Mesh.Define(stage, "/quad")
        mesh.CreatePointsAttr([(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)])
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.CreateDisplayColorAttr([Gf.Vec3f(0.5, 0.3, 0.1)])
        UsdLux.DomeLight.Define(stage, "/dome").CreateIntensityAttr(1.0)
        stage.GetRootLayer().Save()
        self.node = hou.node("/stage").createNode("sublayer", "hrt_installed_smoke")
        self.node.parm("filepath1").set(str(scene_path))
        self.node.setDisplayFlag(True)
        self.result["fixture"] = str(scene_path)

    def select_karma(self):
        self.viewer.setHydraRenderer("Karma CPU")
        self.viewer.curViewport().draw()

    def pause_karma(self):
        self.viewer.setRendererPaused(True)

    def check_karma(self):
        self.ready(self.viewer.isRendererPaused(), "Preparing paused Karma CPU")
        self.previous = self.viewer.currentHydraRenderer()
        self.previous_paused = self.viewer.isRendererPaused()
        self.result["previous_renderer"] = self.previous
        self.result["previous_paused"] = self.previous_paused

    def open_panel(self):
        self.panel = hou.ui.curDesktop().createFloatingPaneTab(
            hou.paneTabType.PythonPanel, size=(440, 500),
            python_panel_interface="RT_Toggle", immediate=True)

    def target_viewer(self):
        self.widget = self.panel.activeInterfaceRootWidget()
        assert not self.panel.activeInterfaceScriptErrors(), self.panel.activeInterfaceScriptErrors()
        self.ready(self.widget is not None, "Waiting for installed panel")
        combo = self.widget.findChild(QtWidgets.QComboBox, "rt_target_viewer")
        assert combo is not None
        index = combo.findText(self.viewer.name())
        self.ready(index >= 0, "Waiting for target viewer discovery")
        combo.setCurrentIndex(index)

    def button(self, name):
        button = self.widget.findChild(QtWidgets.QPushButton, name)
        assert button is not None and button.isEnabled(), "Button unavailable: " + name
        return button

    def enable(self):
        self.button("rt_render_toggle").click()
        self.viewer.curViewport().draw()
        hou.ui.triggerUpdate()
        hou.ui.postRedrawFence()

    def record(self, name):
        status = self.controller.status(self.viewer)
        item = {"step": name, "elapsed_seconds": time.monotonic() - self.started,
            "controller": dataclasses.asdict(status),
            "native_renderer": self.viewer.currentHydraRenderer(),
            "native_paused": self.viewer.isRendererPaused(),
            "panel_labels": {name: self.widget.findChild(QtWidgets.QLabel, name).text()
                for name in ("rt_actual_renderer", "rt_render_state", "rt_render_error")}}
        if self.other is not None:
            item["other_viewer"] = {"name": self.other.name(),
                "renderer": self.other.currentHydraRenderer(), "paused": self.other.isRendererPaused()}
            assert item["other_viewer"]["renderer"] == "Karma CPU"
            assert not item["other_viewer"]["paused"]
        self.records.append(item)
        self.write()
        return status

    def check_active(self):
        status = self.record("active")
        self.ready(status.state == "active" and not status.busy, status)
        assert status.owned and not status.error, status
        self.verify_loaded_delegate()

    def verify_loaded_delegate(self):
        # Read-only query of this process's loaded module, not registry metadata.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
        kernel.GetModuleFileNameW.restype = ctypes.c_uint
        handle = kernel.GetModuleHandleW("hdRT.dll")
        self.ready(bool(handle), "Waiting for installed native delegate to load")
        buffer = ctypes.create_unicode_buffer(32768)
        count = kernel.GetModuleFileNameW(handle, buffer, len(buffer))
        assert 0 < count < len(buffer), ctypes.get_last_error()
        self.result["loaded_delegate"] = self.verify_file(buffer.value)
        self.write()

    def pause(self):
        self.button("rt_render_pause").click()
        self.record("pause_requested")

    def check_paused(self):
        status = self.record("paused")
        self.ready(status.state == "paused" and not status.busy, status)
        assert not status.error, status

    def resume(self):
        self.button("rt_render_pause").click()

    def recreate_panel(self):
        self.panel.close()
        self.widget = None
        self.open_panel()

    def check_recreated(self):
        status = self.record("recreated_panel")
        self.ready(status.state == "paused" and not status.busy, status)
        assert status.owned and status.previous_renderer == self.previous, status
        assert self.button("rt_render_pause").text() == "Resume"

    def create_other(self):
        self.other = hou.ui.curDesktop().createFloatingPaneTab(
            hou.paneTabType.SceneViewer, size=(500, 450), immediate=True)
        self.other.setPwd(hou.node("/stage"))
        self.other.setCurrentNode(self.node)
        self.other.setHydraRenderer("Karma CPU")
        self.other.setIsCurrentTab()

    def check_other(self):
        assert self.other.isViewingSceneGraph() and self.other != self.viewer
        combo = self.widget.findChild(QtWidgets.QComboBox, "rt_target_viewer")
        assert combo.currentText() == self.viewer.name(), combo.currentText()
        self.record("second_viewer_keeps_target")

    def disable(self):
        self.button("rt_render_toggle").click()

    def check_restored(self):
        status = self.record("restored")
        self.ready(status.state == "off" and not status.owned and not status.busy, status)
        assert self.viewer.currentHydraRenderer() == self.previous
        assert self.viewer.isRendererPaused() == self.previous_paused
        assert not status.error, status

    def run_next(self):
        if self.index >= len(self.steps):
            self.finish(True)
            return
        step = self.steps[self.index]
        if self.deadline is None:
            self.deadline = time.monotonic() + 15.0
        print("RT_INSTALLED_SMOKE {:.3f}s {}".format(
            time.monotonic() - self.started, step.__name__), file=sys.stderr, flush=True)
        try:
            step()
        except Pending as exc:
            if time.monotonic() < self.deadline:
                QtCore.QTimer.singleShot(250, self.run_next)
                return
            self.result.update(failed_step=step.__name__, error="Readback timeout: " + str(exc))
            self.finish(False)
            return
        except Exception:
            self.result.update(failed_step=step.__name__, error=traceback.format_exc())
            self.finish(False)
            return
        self.index += 1
        self.deadline = None
        # A short confirmed-paused dwell permits the independent CUA UI check.
        delay = 8000 if step == self.check_paused else 5000 if step == self.enable else 1000
        QtCore.QTimer.singleShot(delay, self.run_next)

    def finish(self, passed):
        self.result["status"] = "passed" if passed else "failed"
        if not passed and self.viewer is not None and hasattr(self, "controller"):
            try:
                self.result["recovery"] = dataclasses.asdict(self.controller.deactivate(self.viewer))
            except Exception:
                self.result["recovery_error"] = traceback.format_exc()
        self.write()
        for pane in (self.panel, self.other):
            if pane is not None:
                try:
                    pane.close()
                except Exception:
                    pass
        if os.environ.get("RT_INSTALLED_EXIT") == "1" and self.safe_to_exit:
            hou.ui.postEventCallback(lambda: hou.hscript("quit -f {}".format(0 if passed else 1)))
