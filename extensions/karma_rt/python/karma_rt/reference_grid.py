"""Version-gated Houdini 22.0.429 viewport reference grid guard.

Temporarily hides the SceneViewer reference plane and orthographic viewport grids
only on Houdini ``22.0.429``, recording original visibility once per viewer and
restoring cleanly on release without leaking deleted pane references.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

TARGET_HOUDINI_VERSION: tuple[int, int, int] = (22, 0, 429)


def _default_hou_getter() -> Any:
    try:
        import hou  # type: ignore[import-not-found]
    except ImportError:
        return None
    return hou


def _default_same_pane(first: Any, second: Any) -> bool:
    if first is second:
        return True
    if first is None or second is None:
        return False
    try:
        if hasattr(first, "name") and hasattr(second, "name"):
            if first.name() != second.name():
                return False
            if hasattr(first, "pane") and hasattr(second, "pane"):
                pane_a = first.pane()
                pane_b = second.pane()
                if pane_a is not None and pane_b is not None:
                    if hasattr(pane_a, "id") and hasattr(pane_b, "id"):
                        return bool(pane_a.id() == pane_b.id())
            return True
    except Exception:
        return False
    return bool(first == second)


@dataclass(slots=True)
class _ViewportGridSnapshot:
    viewport: Any
    viewport_name: str
    display_ortho_grid: bool


@dataclass(slots=True)
class _EngagedViewerRecord:
    viewer: Any
    ref_plane_visible: bool | None = None
    ortho_grids: list[_ViewportGridSnapshot] = field(default_factory=list)


class ReferenceGridGuard:
    """Hides and restores SceneViewer reference plane/grid strictly on Houdini 22.0.429."""

    def __init__(
        self,
        hou_getter: Callable[[], Any] | None = None,
        same_pane: Callable[[Any, Any], bool] | None = None,
    ) -> None:
        self._hou_getter = hou_getter if hou_getter is not None else _default_hou_getter
        self._same_pane = same_pane if same_pane is not None else _default_same_pane
        self._records: list[_EngagedViewerRecord] = []

    def _get_hou(self) -> Any:
        try:
            return self._hou_getter()
        except Exception:
            return None

    def _deleted_errors(self) -> tuple[type[BaseException], ...]:
        hou = self._get_hou()
        errors: list[type[BaseException]] = [ReferenceError]
        if hou is not None:
            deleted_cls = getattr(hou, "ObjectWasDeleted", None)
            if isinstance(deleted_cls, type) and issubclass(deleted_cls, BaseException):
                errors.append(deleted_cls)
        return tuple(errors)

    def supported(self) -> bool:
        """Return True strictly when running on Houdini 22.0.429."""
        hou = self._get_hou()
        if hou is None:
            return False
        try:
            version = tuple(hou.applicationVersion())
        except Exception:
            return False
        return version == TARGET_HOUDINI_VERSION

    @property
    def engaged_count(self) -> int:
        return len(self._records)

    def _find_record_index(self, viewer: Any) -> int | None:
        deleted_errors = self._deleted_errors()
        stale_indices: list[int] = []
        matched: int | None = None

        for idx, record in enumerate(self._records):
            try:
                if self._same_pane(record.viewer, viewer):
                    matched = idx
                    break
            except deleted_errors:
                stale_indices.append(idx)
            except Exception:
                continue

        if stale_indices:
            for idx in reversed(stale_indices):
                self._records.pop(idx)
            if matched is not None:
                matched -= sum(1 for s in stale_indices if s < matched)

        return matched

    def _iter_viewports(self, viewer: Any) -> list[Any]:
        viewports_fn = getattr(viewer, "viewports", None)
        if callable(viewports_fn):
            vps = viewports_fn()
            if vps:
                return list(vps)
        cur_vp_fn = getattr(viewer, "curViewport", None)
        if callable(cur_vp_fn):
            vp = cur_vp_fn()
            if vp is not None:
                return [vp]
        return []

    def engage(self, viewer: Any) -> bool:
        """Hide reference plane and ortho grids once for ``viewer`` if supported."""
        if viewer is None or not self.supported():
            return False

        if self._find_record_index(viewer) is not None:
            return True

        deleted_errors = self._deleted_errors()
        record = _EngagedViewerRecord(viewer=viewer)

        try:
            ref_plane_fn = getattr(viewer, "referencePlane", None)
            if callable(ref_plane_fn):
                ref_plane = ref_plane_fn()
                if (
                    ref_plane is not None
                    and hasattr(ref_plane, "isVisible")
                    and hasattr(ref_plane, "setIsVisible")
                ):
                    orig_visible = bool(ref_plane.isVisible())
                    record.ref_plane_visible = orig_visible
                    if orig_visible:
                        ref_plane.setIsVisible(False)

            for vp in self._iter_viewports(viewer):
                settings_fn = getattr(vp, "settings", None)
                if not callable(settings_fn):
                    continue
                settings = settings_fn()
                if (
                    settings is None
                    or not hasattr(settings, "displayOrthoGrid")
                    or not hasattr(settings, "setDisplayOrthoGrid")
                ):
                    continue
                orig_ortho = bool(settings.displayOrthoGrid())
                vp_name = ""
                name_fn = getattr(vp, "name", None)
                if callable(name_fn):
                    vp_name = str(name_fn())
                record.ortho_grids.append(
                    _ViewportGridSnapshot(
                        viewport=vp,
                        viewport_name=vp_name,
                        display_ortho_grid=orig_ortho,
                    )
                )
                if orig_ortho:
                    settings.setDisplayOrthoGrid(False)
        except deleted_errors:
            return False
        except Exception as exc:
            logger.debug("ReferenceGridGuard.engage failed: %s", exc)
            self._restore_record(record)
            return False

        self._records.append(record)
        return True

    def _restore_record(self, record: _EngagedViewerRecord) -> bool:
        deleted_errors = self._deleted_errors()
        restored = True

        try:
            if record.ref_plane_visible is not None:
                ref_plane_fn = getattr(record.viewer, "referencePlane", None)
                if callable(ref_plane_fn):
                    ref_plane = ref_plane_fn()
                    if ref_plane is not None and hasattr(ref_plane, "setIsVisible"):
                        ref_plane.setIsVisible(record.ref_plane_visible)
        except deleted_errors:
            return False
        except Exception as exc:
            logger.debug("ReferenceGridGuard referencePlane restore failed: %s", exc)
            restored = False

        live_by_name: dict[str, Any] = {}
        try:
            for vp in self._iter_viewports(record.viewer):
                name_fn = getattr(vp, "name", None)
                if callable(name_fn):
                    live_by_name[str(name_fn())] = vp
        except deleted_errors:
            return False
        except Exception:
            pass

        for snap in record.ortho_grids:
            target_vp = (
                live_by_name.get(snap.viewport_name, snap.viewport)
                if snap.viewport_name
                else snap.viewport
            )
            try:
                settings_fn = getattr(target_vp, "settings", None)
                if callable(settings_fn):
                    settings = settings_fn()
                    if settings is not None and hasattr(settings, "setDisplayOrthoGrid"):
                        settings.setDisplayOrthoGrid(snap.display_ortho_grid)
            except deleted_errors:
                restored = False
            except Exception as exc:
                logger.debug("ReferenceGridGuard ortho grid restore failed: %s", exc)
                restored = False

        return restored

    def release(self, viewer: Any) -> bool:
        """Restore reference plane and ortho grids for ``viewer`` if previously engaged."""
        if viewer is None:
            return False
        idx = self._find_record_index(viewer)
        if idx is None:
            return False
        record = self._records.pop(idx)
        return self._restore_record(record)

    def release_all(self) -> None:
        """Restore all engaged viewers and clear tracked records safely."""
        records = self._records
        self._records = []
        for record in records:
            try:
                self._restore_record(record)
            except Exception as exc:
                logger.debug("ReferenceGridGuard.release_all ignored error: %s", exc)


__all__ = [
    "ReferenceGridGuard",
    "TARGET_HOUDINI_VERSION",
]
