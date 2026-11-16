"""Isolated HOM pause diagnostic, using the smoke fixture from the same scratch.

This bypasses the product panel to compare host pause dispatch across delegates.
Requires the same explicit opt-in, workspace preferences and empty HIP safeguards
as gui_viewer_smoke.py. Records observations; it is not a product pass receipt.
"""
import json
import os
from pathlib import Path
import sys
import time
import traceback
import hou
from hutil.Qt import QtCore

class PauseProbe:
    def __init__(self):
        self.output = Path(os.environ["RT_GUI_SMOKE_OUTPUT"]).resolve()
        assert os.environ.get("RT_GUI_SMOKE_ISOLATED") == "1"
        assert Path(hou.homeHoudiniDirectory()).resolve().is_relative_to(self.output.parent)
        assert hou.hipFile.isNewFile()
        self.records = []
        self.started = time.monotonic()
        self.viewer = next(p for p in hou.ui.curDesktop().paneTabs()
                           if p.type() == hou.paneTabType.SceneViewer)
        node = hou.node("/stage").createNode("sublayer", "hrt_pause_probe")
        node.parm("filepath1").set(str(self.output.parent / "smoke_scene.usda"))
        node.setDisplayFlag(True)
        self.viewer.setPwd(hou.node("/stage"))
        self.viewer.setCurrentNode(node)
        self.viewer.setIsCurrentTab()
        self.steps = [("initial", None), ("select_rt", "HoudiniRT"),
                      ("pause_rt", True), ("observe_rt_paused", None),
                      ("resume_rt", False), ("observe_rt_resumed", None),
                      ("select_karma", "Karma CPU"), ("pause_karma", True),
                      ("observe_karma_paused", None), ("resume_karma", False),
                      ("observe_karma_resumed", None), ("restore", "Houdini VK")]
        self.index = 0
    def write(self, status="running", error=""):
        self.output.write_text(json.dumps({"status": status, "scope": "Native HOM diagnostic only",
            "records": self.records, "error": error}, indent=2)+"\n", encoding="utf-8")
    def step(self):
        if self.index >= len(self.steps):
            self.finish()
            return
        name, action = self.steps[self.index]
        print("RT_PAUSE_PROBE {:.3f}s {}".format(time.monotonic()-self.started, name),
              file=sys.stderr, flush=True)
        try:
            if isinstance(action, str): self.viewer.setHydraRenderer(action)
            elif isinstance(action, bool): self.viewer.setRendererPaused(action)
            self.records.append({"step": name, "elapsed_seconds": time.monotonic()-self.started,
                "renderer": self.viewer.currentHydraRenderer(), "paused": self.viewer.isRendererPaused()})
            self.write()
        except Exception:
            self.finish(traceback.format_exc())
            return
        self.index += 1
        QtCore.QTimer.singleShot(6000 if isinstance(action, str) else 2000, self.step)
    def finish(self, error=""):
        self.viewer.setHydraRenderer("Houdini VK")
        self.write("completed" if not error else "failed", error)
        if os.environ.get("RT_GUI_SMOKE_EXIT") == "1":
            hou.ui.postEventCallback(lambda: hou.hscript("quit -f"))

hou.session._hrt_pause_probe = PauseProbe()
QtCore.QTimer.singleShot(2000, hou.session._hrt_pause_probe.step)
