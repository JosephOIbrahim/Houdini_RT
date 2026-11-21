"""Safe IPv4 wrapper around Houdini's ``hou.clone`` preview connection.

Enforces explicit IPv4 loopback (``127.0.0.1``) on Windows to avoid IPv6
``localhost`` resolution stalls, and degrades honestly to ``"UNAVAILABLE"``
whenever ``hou.clone`` is absent or running in a headless session.
"""

from __future__ import annotations

import inspect
import math
import os
from pathlib import Path
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Sequence

DEFAULT_CLONE_HOST = "127.0.0.1"
UNAVAILABLE = "UNAVAILABLE"

_LOOPBACK_ALIASES = frozenset(
    {
        "",
        "localhost",
        "localhost.localdomain",
        "::1",
        "[::1]",
        "0:0:0:0:0:0:0:1",
        "[0:0:0:0:0:0:0:1]",
    }
)


def normalize_ipv4_host(host: str | None) -> str:
    """Force IPv4 ``127.0.0.1`` instead of ``localhost`` or IPv6 loopback."""
    if host is None:
        return DEFAULT_CLONE_HOST
    cleaned = str(host).strip()
    if cleaned.lower() in _LOOPBACK_ALIASES:
        return DEFAULT_CLONE_HOST
    if cleaned.lower().startswith("localhost:"):
        return DEFAULT_CLONE_HOST
    return cleaned


@dataclass(slots=True)
class CloneStatus:
    """Snapshot of the Houdini clone preview connection state."""

    available: bool
    connected: bool
    host: str
    port: int | None
    resolution_scale: float
    render_region: tuple[float, float, float, float] | None
    image_path: str | None
    percent_complete: float | None
    message: str
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "resolution_scale": self.resolution_scale,
            "render_region": list(self.render_region) if self.render_region is not None else None,
            "image_path": self.image_path,
            "percent_complete": self.percent_complete,
            "message": self.message,
            "error": self.error,
        }


def _try_import_hou() -> Any | None:
    try:
        import hou  # type: ignore[import-not-found]

        return hou
    except Exception:
        return None


def _accepts_kwarg(fn: Any, kwarg_name: str) -> bool:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for param in sig.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == kwarg_name and param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
    return False


def _positional_param_count(fn: Any) -> int | None:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    count = 0
    for param in sig.parameters.values():
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            return None
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            count += 1
    return count


def _read_optional_attr(obj: Any, names: Sequence[str]) -> Any:
    for name in names:
        candidate = getattr(obj, name, None)
        if candidate is None:
            continue
        if callable(candidate):
            try:
                val = candidate()
                if val is not None:
                    return val
            except Exception:
                continue
        else:
            return candidate
    return None


def _validate_render_region(
    region: Sequence[float] | None,
) -> tuple[tuple[float, float, float, float] | None, str | None]:
    if region is None:
        return None, None
    try:
        coords = tuple(float(v) for v in region)
    except Exception:
        return None, "render_region must be a 4-element sequence of floats (xmin, ymin, xmax, ymax)"
    if len(coords) != 4:
        return None, "render_region must contain exactly 4 floats (xmin, ymin, xmax, ymax)"
    if any(math.isnan(v) or math.isinf(v) for v in coords):
        return None, "render_region values must be finite floats"
    xmin, ymin, xmax, ymax = coords
    if not (0.0 <= xmin < xmax <= 1.0 and 0.0 <= ymin < ymax <= 1.0):
        return None, "render_region must satisfy 0.0 <= xmin < xmax <= 1.0 and 0.0 <= ymin < ymax <= 1.0"
    return (xmin, ymin, xmax, ymax), None


class ClonePreviewManager:
    """Lifecycle and parameter manager for a ``hou.clone`` preview session."""

    def __init__(
        self,
        hou_module: Any | None = None,
        host: str = DEFAULT_CLONE_HOST,
    ) -> None:
        self._hou_injected = hou_module is not None
        self._hou = hou_module if hou_module is not None else _try_import_hou()
        self._host: str = normalize_ipv4_host(host)
        self._port: int | None = None
        self._connection: Any | None = None
        self._resolution_scale: float = 1.0
        self._render_region: tuple[float, float, float, float] | None = None
        self._image_path: str | None = None
        self._percent_complete: float | None = None
        self._process_updates: bool = True
        self._worker_proc: subprocess.Popen[str] | None = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def connection(self) -> Any | None:
        return self._connection

    @property
    def available(self) -> bool:
        clone_mod, _ = self._resolve_clone_module()
        return clone_mod is not None

    @property
    def connected(self) -> bool:
        return self._is_connected()

    def _resolve_clone_module(self) -> tuple[Any | None, str | None]:
        if self._hou is None:
            return None, "hou module is not available in this Python environment"

        is_ui_available = getattr(self._hou, "isUIAvailable", None)
        if callable(is_ui_available):
            try:
                if not is_ui_available():
                    return None, "hou.clone preview is unavailable in headless mode (isUIAvailable() is False)"
            except Exception as exc:
                return None, f"Failed to query hou.isUIAvailable(): {exc}"

        clone_mod = getattr(self._hou, "clone", None)
        if clone_mod is None:
            return None, "hou.clone module is absent in this Houdini build"
        if not callable(getattr(clone_mod, "createClone", None)):
            return None, "hou.clone.createClone() is not exposed"
        return clone_mod, None

    def _is_connected(self) -> bool:
        if self._connection is None:
            return False
        is_conn_fn = getattr(self._connection, "isConnected", None)
        if callable(is_conn_fn):
            try:
                return bool(is_conn_fn())
            except Exception:
                return False
        return True

    def _refresh_connection_telemetry(self) -> None:
        if self._connection is None:
            self._port = None
            return

        raw_host = _read_optional_attr(self._connection, ("host", "hostname"))
        if isinstance(raw_host, str) and raw_host.strip():
            self._host = normalize_ipv4_host(raw_host)

        raw_port = _read_optional_attr(self._connection, ("port",))
        if isinstance(raw_port, int) and not isinstance(raw_port, bool):
            self._port = raw_port

        raw_progress = _read_optional_attr(
            self._connection, ("percentComplete", "renderProgress", "progress")
        )
        if isinstance(raw_progress, (int, float)) and not isinstance(raw_progress, bool):
            val = float(raw_progress)
            if not (math.isnan(val) or math.isinf(val)):
                self._percent_complete = round(val, 2)

        raw_image = _read_optional_attr(
            self._connection, ("imagePath", "snapshotPath", "outputImagePath")
        )
        if isinstance(raw_image, str) and raw_image:
            self._image_path = raw_image

    def _unavailable_status(self, reason: str) -> CloneStatus:
        return CloneStatus(
            available=False,
            connected=False,
            host=self._host,
            port=None,
            resolution_scale=self._resolution_scale,
            render_region=self._render_region,
            image_path=self._image_path,
            percent_complete=None,
            message=UNAVAILABLE,
            error=reason,
        )

    def _build_status(self, message: str, error: str | None = None) -> CloneStatus:
        clone_mod, reason = self._resolve_clone_module()
        if clone_mod is None:
            return self._unavailable_status(reason or "hou.clone is unavailable")
        self._refresh_connection_telemetry()
        return CloneStatus(
            available=True,
            connected=self._is_connected(),
            host=self._host,
            port=self._port,
            resolution_scale=self._resolution_scale,
            render_region=self._render_region,
            image_path=self._image_path,
            percent_complete=self._percent_complete,
            message=message,
            error=error,
        )

    def status(self) -> CloneStatus:
        """Return the current honest ``CloneStatus`` snapshot."""
        clone_mod, reason = self._resolve_clone_module()
        if clone_mod is None:
            return self._unavailable_status(reason or "hou.clone is unavailable")
        self._refresh_connection_telemetry()
        is_conn = self._is_connected()
        return CloneStatus(
            available=True,
            connected=is_conn,
            host=self._host,
            port=self._port,
            resolution_scale=self._resolution_scale,
            render_region=self._render_region,
            image_path=self._image_path,
            percent_complete=self._percent_complete,
            message="CONNECTED" if is_conn else "DISCONNECTED",
            error=None,
        )

    def connect(
        self,
        *,
        lop_node: Any | None = None,
        camera_path: str | None = None,
        renderer: str | None = None,
        resolution_scale: float | None = None,
        render_region: Sequence[float] | None = None,
        process_updates: bool | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> CloneStatus:
        """Create and optionally configure a ``hou.clone`` preview connection."""
        if host is not None:
            self._host = normalize_ipv4_host(host)

        clone_mod, reason = self._resolve_clone_module()
        if clone_mod is None:
            return self._unavailable_status(reason or "hou.clone is unavailable")

        if self._connection is not None and self._is_connected():
            conn = self._connection
        else:
            create_clone = clone_mod.createClone
            kwargs: dict[str, Any] = {}
            if _accepts_kwarg(create_clone, "host"):
                kwargs["host"] = self._host
            if port is not None and _accepts_kwarg(create_clone, "port"):
                kwargs["port"] = int(port)

            try:
                conn = create_clone(**kwargs) if kwargs else create_clone()
            except TypeError:
                try:
                    conn = create_clone()
                except Exception as exc:
                    self._connection = None
                    return self._build_status(message="ERROR", error=f"createClone failed: {exc}")
            except Exception as exc:
                self._connection = None
                return self._build_status(message="ERROR", error=f"createClone failed: {exc}")

            if conn is None:
                self._connection = None
                return self._build_status(
                    message=UNAVAILABLE,
                    error="hou.clone.createClone() returned None",
                )
            self._connection = conn
            if port is not None and self._port is None:
                self._port = int(port)

        self._refresh_connection_telemetry()

        if lop_node is not None:
            st = self.set_lop_node(lop_node)
            if st.error:
                return st
        if camera_path is not None:
            st = self.set_camera_path(camera_path)
            if st.error:
                return st
        if renderer is not None:
            st = self.set_renderer(renderer)
            if st.error:
                return st
        if resolution_scale is not None:
            st = self.set_resolution_scale(resolution_scale)
            if st.error:
                return st
        if render_region is not None:
            st = self.set_render_region(render_region)
            if st.error:
                return st
        if process_updates is not None:
            st = self.set_process_updates(process_updates)
            if st.error:
                return st

        return self._build_status(message="CONNECTED")

    def _resolve_hython_executable(self) -> Path | None:
        hfs = os.environ.get("HFS", "").strip()
        if not hfs and self._hou is not None:
            try:
                hfs = str(self._hou.getenv("HFS") or "").strip()
            except Exception:
                hfs = ""
        if not hfs:
            return None
        exe = "hython.exe" if os.name == "nt" else "hython"
        candidate = Path(hfs) / "bin" / exe
        return candidate if candidate.is_file() else None

    def launch_local_clone(
        self,
        *,
        lop_node: Any | None = None,
        camera_path: str | None = None,
        renderer: str | None = "BRAY_HdKarmaXPU",
        resolution_scale: float = 0.5,
        render_region: Sequence[float] | None = None,
        startup_timeout_sec: float = 15.0,
    ) -> CloneStatus:
        """Spawn a headless local ``hython`` clone worker on ``127.0.0.1`` and connect."""
        clone_mod, reason = self._resolve_clone_module()
        if clone_mod is None:
            return self._unavailable_status(reason or "hou.clone is unavailable")

        hython_exe = self._resolve_hython_executable()
        if hython_exe is None:
            return self._build_status(
                message=UNAVAILABLE,
                error="Could not locate $HFS/bin/hython to spawn local clone worker.",
            )

        self._terminate_worker()
        clone_script = (
            "import hou; "
            "hou.clone.runAsClone("
            "start_port=0, end_port=-1, block=True, print_port=True, "
            "connection_timeout_seconds=30, initialize_opengl=False)"
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                [str(hython_exe), "-c", clone_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creationflags,
            )
        except Exception as exc:
            return self._build_status(message="ERROR", error=f"Failed to launch hython clone: {exc}")

        self._worker_proc = proc
        detected_port: int | None = None
        deadline = time.monotonic() + max(1.0, float(startup_timeout_sec))
        port_re = re.compile(r"HOUDINI_CLONE_PORT\s*=\s*(\d+)")

        try:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                if proc.stdout is None:
                    break
                line = proc.stdout.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                match = port_re.search(line)
                if match:
                    detected_port = int(match.group(1))
                    break
        except Exception as exc:
            self._terminate_worker()
            return self._build_status(message="ERROR", error=f"Clone port handshake failed: {exc}")

        if detected_port is None:
            self._terminate_worker()
            return self._build_status(
                message="ERROR",
                error=f"Local hython clone did not emit HOUDINI_CLONE_PORT within {startup_timeout_sec:.1f}s.",
            )

        status = self.connect(
            lop_node=lop_node,
            camera_path=camera_path,
            renderer=renderer,
            resolution_scale=resolution_scale,
            render_region=render_region,
            process_updates=True,
            host=DEFAULT_CLONE_HOST,
            port=detected_port,
        )
        if status.error:
            self._terminate_worker()
        return status

    def _terminate_worker(self) -> None:
        proc = self._worker_proc
        self._worker_proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except Exception:
                    proc.kill()
        except Exception:
            pass

    def _require_connection(self) -> tuple[Any | None, CloneStatus | None]:
        clone_mod, reason = self._resolve_clone_module()
        if clone_mod is None:
            return None, self._unavailable_status(reason or "hou.clone is unavailable")
        if self._connection is None or not self._is_connected():
            return None, self._build_status(
                message="DISCONNECTED",
                error="No active hou.clone connection; call connect() first",
            )
        return self._connection, None

    def set_lop_node(self, lop_node: Any) -> CloneStatus:
        conn, err_status = self._require_connection()
        if err_status is not None:
            return err_status
        resolved = lop_node
        if isinstance(lop_node, str) and self._hou is not None:
            node_fn = getattr(self._hou, "node", None)
            if callable(node_fn):
                try:
                    found = node_fn(lop_node)
                    if found is not None:
                        resolved = found
                except Exception:
                    pass
        try:
            conn.setLopNode(resolved)
        except Exception as exc:
            return self._build_status(message="ERROR", error=f"setLopNode failed: {exc}")
        return self._build_status(message="LOP_NODE_UPDATED")

    def set_camera_path(self, camera_path: str) -> CloneStatus:
        conn, err_status = self._require_connection()
        if err_status is not None:
            return err_status
        try:
            conn.setCameraPath(str(camera_path))
        except Exception as exc:
            return self._build_status(message="ERROR", error=f"setCameraPath failed: {exc}")
        return self._build_status(message="CAMERA_UPDATED")

    def set_renderer(self, renderer_name: str) -> CloneStatus:
        conn, err_status = self._require_connection()
        if err_status is not None:
            return err_status
        try:
            conn.setRenderer(str(renderer_name))
        except Exception as exc:
            return self._build_status(message="ERROR", error=f"setRenderer failed: {exc}")
        return self._build_status(message="RENDERER_UPDATED")

    def set_resolution_scale(self, scale: float) -> CloneStatus:
        try:
            parsed = float(scale)
        except Exception:
            return self._build_status(message="ERROR", error="resolution_scale must be numeric")
        if math.isnan(parsed) or math.isinf(parsed) or parsed <= 0.0:
            return self._build_status(
                message="ERROR", error="resolution_scale must be a finite float > 0"
            )

        self._resolution_scale = parsed
        conn, err_status = self._require_connection()
        if err_status is not None:
            return err_status
        try:
            conn.setResolutionScale(parsed)
        except Exception as exc:
            return self._build_status(message="ERROR", error=f"setResolutionScale failed: {exc}")
        return self._build_status(message="RESOLUTION_SCALE_UPDATED")

    def set_render_region(self, region: Sequence[float] | None) -> CloneStatus:
        validated, validation_err = _validate_render_region(region)
        if validation_err is not None:
            return self._build_status(message="ERROR", error=validation_err)

        self._render_region = validated
        conn, err_status = self._require_connection()
        if err_status is not None:
            return err_status

        setter = getattr(conn, "setRenderRegion", None)
        if not callable(setter):
            return self._build_status(
                message="ERROR", error="Clone connection does not expose setRenderRegion()"
            )

        try:
            if validated is None:
                param_count = _positional_param_count(setter)
                if param_count == 4:
                    setter(0.0, 0.0, 1.0, 1.0)
                else:
                    try:
                        setter(None)
                    except TypeError:
                        try:
                            setter((0.0, 0.0, 1.0, 1.0))
                        except TypeError:
                            setter(0.0, 0.0, 1.0, 1.0)
            else:
                param_count = _positional_param_count(setter)
                if param_count == 4:
                    setter(*validated)
                else:
                    try:
                        setter(validated)
                    except TypeError:
                        setter(*validated)
        except Exception as exc:
            return self._build_status(message="ERROR", error=f"setRenderRegion failed: {exc}")
        return self._build_status(message="RENDER_REGION_UPDATED")

    def set_process_updates(self, enabled: bool) -> CloneStatus:
        flag = bool(enabled)
        self._process_updates = flag
        conn, err_status = self._require_connection()
        if err_status is not None:
            return err_status
        try:
            conn.setProcessUpdates(flag)
        except Exception as exc:
            return self._build_status(message="ERROR", error=f"setProcessUpdates failed: {exc}")
        return self._build_status(
            message="UPDATES_ENABLED" if flag else "UPDATES_PAUSED"
        )

    def create_snapshot(
        self, image_path: str | os.PathLike[str] | None = None
    ) -> CloneStatus:
        conn, err_status = self._require_connection()
        if err_status is not None:
            return err_status

        snapshot_fn = getattr(conn, "createSnapshot", None)
        if not callable(snapshot_fn):
            return self._build_status(
                message="ERROR", error="Clone connection does not expose createSnapshot()"
            )

        requested_path = os.fspath(image_path) if image_path is not None else None
        try:
            if requested_path is not None:
                try:
                    result = snapshot_fn(requested_path)
                except TypeError:
                    result = snapshot_fn()
            else:
                result = snapshot_fn()
        except Exception as exc:
            return self._build_status(message="ERROR", error=f"createSnapshot failed: {exc}")

        if isinstance(result, str) and result:
            self._image_path = result
        elif requested_path is not None:
            self._image_path = requested_path

        return self._build_status(message="SNAPSHOT_CREATED")

    def disconnect(self) -> CloneStatus:
        clone_mod, reason = self._resolve_clone_module()
        if clone_mod is None:
            self._connection = None
            self._port = None
            return self._unavailable_status(reason or "hou.clone is unavailable")

        error_msg: str | None = None
        if self._connection is not None:
            disconnect_fn = getattr(self._connection, "disconnect", None)
            if callable(disconnect_fn):
                try:
                    disconnect_fn()
                except Exception as exc:
                    error_msg = f"disconnect failed: {exc}"
            self._connection = None
            self._port = None
            self._percent_complete = None

        self._terminate_worker()
        return self._build_status(message="DISCONNECTED", error=error_msg)

    def __enter__(self) -> ClonePreviewManager:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.disconnect()


__all__ = [
    "DEFAULT_CLONE_HOST",
    "UNAVAILABLE",
    "normalize_ipv4_host",
    "CloneStatus",
    "ClonePreviewManager",
]
