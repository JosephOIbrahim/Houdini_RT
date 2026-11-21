"""Opt-in Houdini 22 interactive GUI startup hook for KarmaRT.

Runs ONLY in interactive Houdini GUI sessions (never in headless hython) and
remains a zero-cost no-op unless ``KARMA_RT_AUTORUN_MODE`` is explicitly set.
"""
from __future__ import annotations

import os

_MODE = os.environ.get("KARMA_RT_AUTORUN_MODE", "").strip().lower()
if _MODE in ("gui_smoke", "open_demo"):
    try:
        from karma_rt.gui_smoke_runner import schedule_autorun

        schedule_autorun(_MODE)
    except Exception as exc:
        try:
            import hou

            hou.ui.setStatusMessage(
                f"Karma RT autorun hook failed: {exc}",
                severity=hou.severityType.Warning,
            )
        except Exception:
            pass
