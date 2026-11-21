"""Startup helper passed to Houdini 22 GUI: switches to Solaris desktop and opens the Karma RT panel."""
from __future__ import annotations

import hou


def _setup_workspace() -> None:
    try:
        desktop = hou.ui.desktop("Solaris")
        if desktop is not None:
            desktop.setAsCurrent()
    except Exception:
        pass

    try:
        stage = hou.node("/stage")
        if stage is not None:
            krs = stage.node("production_karma_settings")
            if krs is not None:
                krs.setDisplayFlag(True)
                krs.setCurrent(True, clear_all_selected=True)
            for pane in hou.ui.paneTabs():
                if pane.type() == hou.paneTabType.SceneViewer:
                    pane.setPwd(stage)
                    try:
                        vp = pane.curViewport()
                        vp.setCamera("/shot/cam/main")
                    except Exception:
                        pass
                    break
    except Exception:
        pass

    try:
        from karma_rt.ui.shelf_actions import open_panel
        open_panel()
    except Exception as exc:
        try:
            hou.ui.setStatusMessage(f"Karma RT startup note: {exc}", severity=hou.severityType.Warning)
        except Exception:
            pass


try:
    import hdefereval
    hdefereval.executeDeferred(_setup_workspace)
except Exception:
    _setup_workspace()
