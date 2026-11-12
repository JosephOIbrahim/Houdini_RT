"""Tests for sidecar mode in the RT toggle adapter.

Runs on CPython 3.12 (and 3.14) with no sidecar process, no Houdini, no
``hou``, and no import of the S1/S2 modules:

* the supervisor is a fake with the ``SidecarSupervisor`` public surface;
* ``/health`` is an injected callable returning ``(code, body)``;
* the clock is fake, so timeout paths are deterministic and instant.

The one test that touches the real repo adapter is mock-level: it injects a
fake compatibility module and only appends/removes a temporary directory on
``sys.path``. It never imports ``hou`` and never starts anything.

Run::

    cd C:\\Users\\User\\AppData\\Local\\Temp\\rt-sidecar\\work\\integration
    C:\\Users\\User\\AppData\\Local\\Programs\\Python\\Python312\\python.exe -m pytest -q
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from rt_runtime import sidecar_adapter as sa  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes: S1 supervisor result/report shapes, injected health, fake clock
# ---------------------------------------------------------------------------


class FakeClock:
    """Monotonic stand-in that advances only when ``sleep`` is called."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, float(seconds))


@dataclass
class FakeReport:
    state: str
    pid: int | None = None
    heartbeat_age: float | None = 0.1
    detail: str = ""
    status: dict | None = None
    lock: dict | None = None
    log_tail: list = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.state in ("starting", "ready", "stopping")

    @property
    def runtime_state(self) -> str:
        if self.running:
            return "on"
        if self.state in ("absent", "stopped"):
            return "off"
        return "error"


@dataclass
class FakeResult:
    ok: bool = False
    state: str = "absent"
    changed: bool = False
    pid: int | None = None
    reasons: list = field(default_factory=list)
    error: str | None = None

    @property
    def runtime_state(self) -> str:
        return FakeReport(self.state).runtime_state


class FakeSupervisor:
    """Duck-typed ``SidecarSupervisor``: probe / start / stop only."""

    def __init__(
        self,
        *,
        report: FakeReport,
        start_result: FakeResult | None = None,
        report_after_start: FakeReport | None = None,
        stop_result: FakeResult | None = None,
        report_after_stop: FakeReport | None = None,
    ) -> None:
        self.report = report
        self.start_result = start_result
        self.report_after_start = report_after_start
        self.stop_result = stop_result
        self.report_after_stop = report_after_stop
        self.kwargs: dict = {}
        self.started = False
        self.stopped = False
        self.start_calls = 0
        self.stop_calls = 0

    def probe(self) -> FakeReport:
        return self.report

    def start(self, *, force: bool = False) -> FakeResult:
        self.start_calls += 1
        self.started = True
        if self.report_after_start is not None:
            self.report = self.report_after_start
        if self.start_result is not None:
            return self.start_result
        return FakeResult(True, "ready", True, self.report.pid, ["started"])

    def stop(self, *, force: bool = False) -> FakeResult:
        self.stop_calls += 1
        self.stopped = True
        if self.report_after_stop is not None:
            self.report = self.report_after_stop
        if self.stop_result is not None:
            return self.stop_result
        return FakeResult(True, "stopped", True, self.report.pid, ["stopped"])


def starting_report() -> FakeReport:
    return FakeReport("starting", pid=None, heartbeat_age=None, detail="initialising")


def ready_report(pid: int = 4242, port: int = 55555, version: str = "3.14.2") -> FakeReport:
    document = {
        "schema": 1,
        "pid": pid,
        "state": "ready",
        "host": "127.0.0.1",
        "port": port,
        "python_version": version,
        "python_implementation": "cpython",
        "sys_prefix": "C:\\Python314",
        "last_error": None,
        "stop_reason": None,
        "heartbeat_seq": 3,
    }
    return FakeReport(
        "ready", pid=pid, heartbeat_age=0.1, detail="ready", status=document
    )


def stopped_report(pid: int = 4242) -> FakeReport:
    return FakeReport(
        "stopped",
        pid=pid,
        heartbeat_age=None,
        detail="graceful",
        status={"pid": pid, "state": "stopped", "port": None, "python_version": "3.14.2"},
    )


def health_ok(base_url: str, timeout: float) -> tuple[int, dict]:
    return (
        200,
        {
            "ok": True,
            "status": "ready",
            "pid": 4242,
            "python_version": "3.14.2",
        },
    )


def health_500(base_url: str, timeout: float) -> tuple[int, dict]:
    return 500, {"ok": False, "error": "internal_error"}


# ---------------------------------------------------------------------------
# Tree + settings fixtures
# ---------------------------------------------------------------------------


def make_settings(tmp_path: Path, **overrides) -> sa.SidecarSettings:
    runtime = tmp_path / "runtime"
    site = runtime / "Lib" / "site-packages"
    package = site / "pydantic_core"
    package.mkdir(parents=True)
    (package / "_pydantic_core.cp314-win_amd64.pyd").write_bytes(b"\0")
    (site / "pydantic_core-2.41.5.dist-info").mkdir()

    base = tmp_path / "base" / "Lib" / "site-packages"
    base_package = base / "pydantic_core"
    base_package.mkdir(parents=True)
    (base_package / "_pydantic_core.cp314-win_amd64.pyd").write_bytes(b"\0")
    (base / "pydantic_core-2.50.4.dist-info").mkdir()

    launcher = tmp_path / "python314.exe"
    launcher.write_bytes(b"")
    host = tmp_path / "host_stub.py"
    host.write_text("# fake host\n", encoding="utf-8")

    settings = sa.SidecarSettings(
        instance="default",
        launcher=launcher,
        host_script=host,
        state_dir=tmp_path / "state",
        runtime_home=runtime,
        site_packages=site,
        base_site_packages=base,
        health_timeout=0.5,
        health_interval=0.1,
        start_timeout=2.0,
    )
    if overrides:
        settings = replace(settings, **overrides)
    return settings


def make_adapter(
    settings: sa.SidecarSettings,
    supervisor: FakeSupervisor,
    *,
    health=health_ok,
    integrity=None,
    clock: FakeClock | None = None,
) -> sa.SidecarAdapter:
    clock = clock or FakeClock()

    def factory(**kwargs):
        supervisor.kwargs = kwargs
        return supervisor

    return sa.SidecarAdapter(
        "houdini_rt",
        settings,
        supervisor_factory=factory,
        health_checker=health,
        integrity_checker=integrity,
        clock=clock.now,
        sleep=clock.sleep,
    )


BLOCKED = sa.IntegrityReport(
    ok=False,
    blockers=(
        "runtime pydantic_core 2.41.5 has no compiled extension "
        "(cp314 binary missing); runtime-first would shadow a working base copy",
    ),
)


# ---------------------------------------------------------------------------
# activate: success
# ---------------------------------------------------------------------------


def test_success_maps_to_on(tmp_path):
    supervisor = FakeSupervisor(
        report=starting_report(),
        start_result=FakeResult(True, "ready", True, 4242, ["started pid 4242"]),
        report_after_start=ready_report(),
    )
    adapter = make_adapter(make_settings(tmp_path), supervisor)

    result = adapter.activate()

    assert result.ok is True
    assert result.state.value == "on"
    assert result.changed is True
    assert result.error is None
    joined = " ".join(result.reasons)
    assert "/health" in joined
    assert "runtime-first" in joined
    assert "3.14.2" in joined
    assert adapter.status().value == "on"
    assert supervisor.kwargs["env"]["RT_SIDECAR_PREFER_RUNTIME"] == "1"
    assert supervisor.kwargs["env"]["RT_SIDECAR_PACKAGE_PRECEDENCE"] == "runtime-first"
    assert supervisor.kwargs["expect_python"] == "3.14"
    assert supervisor.kwargs["launcher"][1:] == ["-I", "-X", "utf8", "-u"]


def test_require_health_starts_the_control_surface(tmp_path):
    supervisor = FakeSupervisor(
        report=starting_report(),
        start_result=FakeResult(True, "ready", True, 4242, ["started"]),
        report_after_start=ready_report(),
    )
    adapter = make_adapter(make_settings(tmp_path), supervisor)

    result = adapter.activate()

    assert result.ok is True
    assert supervisor.kwargs["serve"] is True


def test_no_health_requirement_leaves_the_host_unserved(tmp_path):
    supervisor = FakeSupervisor(
        report=starting_report(),
        start_result=FakeResult(True, "ready", True, 4242, ["started"]),
        report_after_start=ready_report(),
    )
    adapter = make_adapter(
        make_settings(tmp_path, require_health=False), supervisor
    )

    result = adapter.activate()

    assert result.ok is True
    assert supervisor.kwargs["serve"] is False


def test_already_running_is_idempotent(tmp_path):
    supervisor = FakeSupervisor(report=ready_report())
    adapter = make_adapter(make_settings(tmp_path), supervisor)

    result = adapter.activate()

    assert result.ok is True
    assert result.state.value == "on"
    assert result.changed is False
    assert supervisor.start_calls == 0
    assert any("already running" in reason for reason in result.reasons)


# ---------------------------------------------------------------------------
# activate: failure mapping
# ---------------------------------------------------------------------------


def test_start_timeout_maps_to_error(tmp_path):
    supervisor = FakeSupervisor(
        report=starting_report(),
        start_result=FakeResult(
            False,
            "starting",
            False,
            None,
            ["waited 10.0s"],
            "sidecar did not become ready within 10.0s",
        ),
        report_after_start=starting_report(),
    )
    adapter = make_adapter(make_settings(tmp_path), supervisor)

    result = adapter.activate()

    assert result.ok is False
    assert result.state.value == "error"
    assert result.changed is False
    assert "did not become ready" in (result.error or "")
    assert any("supervisor state: starting" in reason for reason in result.reasons)


def test_health_failure_maps_to_error_and_rolls_back(tmp_path):
    supervisor = FakeSupervisor(
        report=starting_report(),
        start_result=FakeResult(True, "ready", True, 4242, ["started"]),
        report_after_start=ready_report(),
        stop_result=FakeResult(True, "stopped", True, 4242, ["stopped gracefully"]),
        report_after_stop=stopped_report(),
    )
    adapter = make_adapter(make_settings(tmp_path), supervisor, health=health_500)

    result = adapter.activate()

    assert result.ok is False
    assert result.state.value == "error"
    assert result.changed is True
    assert supervisor.stopped is True
    assert "rolled back" in " ".join(result.reasons)
    assert result.error


def test_ready_but_unhealthy_is_reported_not_killed(tmp_path):
    supervisor = FakeSupervisor(report=ready_report())
    adapter = make_adapter(make_settings(tmp_path), supervisor, health=health_500)

    result = adapter.activate()

    assert result.ok is False
    assert result.state.value == "error"
    assert result.changed is False
    assert supervisor.stopped is False
    assert any("deactivate()" in reason for reason in result.reasons)


def test_missing_port_with_health_required_maps_to_error(tmp_path):
    supervisor = FakeSupervisor(
        report=starting_report(),
        start_result=FakeResult(True, "ready", True, 4242, ["started"]),
        report_after_start=ready_report(port=0),
        report_after_stop=stopped_report(),
        stop_result=FakeResult(True, "stopped", True, 4242, ["stopped"]),
    )
    adapter = make_adapter(make_settings(tmp_path), supervisor)

    result = adapter.activate()

    assert result.ok is False
    assert result.state.value == "error"
    assert "no port" in " ".join(result.reasons)


# ---------------------------------------------------------------------------
# deactivate
# ---------------------------------------------------------------------------


def test_stop_failure_maps_to_error(tmp_path):
    supervisor = FakeSupervisor(
        report=ready_report(),
        stop_result=FakeResult(
            False,
            "error",
            False,
            4242,
            ["process survived the graceful window and terminate"],
            "could not kill pid 4242",
        ),
    )
    adapter = make_adapter(make_settings(tmp_path), supervisor)

    result = adapter.deactivate()

    assert result.ok is False
    assert result.state.value == "error"
    assert "could not kill pid 4242" in (result.error or "")


def test_stop_ok_but_still_alive_maps_to_error(tmp_path):
    supervisor = FakeSupervisor(
        report=ready_report(),
        stop_result=FakeResult(True, "stopped", True, 4242, ["stopped"]),
        report_after_stop=FakeReport("unresponsive", pid=4242, detail="alive"),
    )
    adapter = make_adapter(make_settings(tmp_path), supervisor)

    result = adapter.deactivate()

    assert result.ok is False
    assert result.state.value == "error"
    assert result.changed is True
    assert "still 'unresponsive'" in " ".join(result.reasons)


def test_stop_success_removes_leftovers_and_reports_off(tmp_path):
    settings = make_settings(tmp_path)
    state_dir = Path(settings.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "token").write_text("leftover", encoding="utf-8")
    (state_dir / "sidecar.lock").write_text("{}", encoding="utf-8")

    supervisor = FakeSupervisor(
        report=ready_report(),
        stop_result=FakeResult(True, "stopped", True, 4242, ["stopped gracefully"]),
        report_after_stop=stopped_report(),
    )
    adapter = make_adapter(settings, supervisor)

    result = adapter.deactivate()

    assert result.ok is True
    assert result.state.value == "off"
    assert result.changed is True
    assert not (state_dir / "token").exists()
    assert not (state_dir / "sidecar.lock").exists()
    assert any("removed leftover token" in reason for reason in result.reasons)


def test_deactivate_when_absent_is_a_no_op(tmp_path):
    supervisor = FakeSupervisor(report=FakeReport("absent", detail="no sidecar"))
    adapter = make_adapter(make_settings(tmp_path), supervisor)

    result = adapter.deactivate()

    assert result.ok is True
    assert result.state.value == "off"
    assert result.changed is False
    assert supervisor.stop_calls == 0


# ---------------------------------------------------------------------------
# Package precedence
# ---------------------------------------------------------------------------


def test_degraded_base_first_is_flagged(tmp_path):
    settings = make_settings(tmp_path, allow_degraded_base_first=True)
    supervisor = FakeSupervisor(
        report=starting_report(),
        start_result=FakeResult(True, "ready", True, 4242, ["started"]),
        report_after_start=ready_report(),
    )
    adapter = make_adapter(settings, supervisor, integrity=lambda _settings: BLOCKED)

    result = adapter.activate()

    assert result.ok is True
    assert result.state.value == "on"
    assert any(reason.startswith("DEGRADED:") for reason in result.reasons)

    detail = adapter.status_detail()
    assert detail["package_precedence"] == "base-first"
    assert detail["package_precedence_requested"] == "runtime-first"
    assert detail["precedence_degraded"] is True

    assert supervisor.kwargs["env"]["RT_SIDECAR_PACKAGE_PRECEDENCE"] == "base-first"
    assert "RT_SIDECAR_PREFER_RUNTIME" not in supervisor.kwargs["env"]


def test_runtime_first_blocked_without_flag_is_refused(tmp_path):
    settings = make_settings(tmp_path)  # allow_degraded_base_first defaults False
    supervisor = FakeSupervisor(report=starting_report())
    adapter = make_adapter(settings, supervisor, integrity=lambda _settings: BLOCKED)

    result = adapter.activate()

    assert result.ok is False
    assert result.state.value == "off"
    assert result.changed is False
    assert result.error is None
    assert supervisor.start_calls == 0
    assert any("allow_degraded_base_first" in reason for reason in result.reasons)


def test_base_first_without_flag_is_refused(tmp_path):
    settings = make_settings(tmp_path, package_precedence="base-first")
    supervisor = FakeSupervisor(report=starting_report())
    adapter = make_adapter(settings, supervisor)

    result = adapter.activate()

    assert result.ok is False
    assert result.state.value == "off"
    assert result.changed is False
    assert supervisor.start_calls == 0
    assert any("allow_degraded_base_first" in reason for reason in result.reasons)


def test_default_integrity_detects_a_missing_binary(tmp_path):
    settings = make_settings(tmp_path)
    runtime_binary = (
        Path(settings.site_packages)
        / "pydantic_core"
        / "_pydantic_core.cp314-win_amd64.pyd"
    )
    runtime_binary.unlink()

    report = sa.default_integrity(settings)
    assert report.ok is False
    assert any("compiled extension" in blocker for blocker in report.blockers)
    assert report.runtime["pydantic_core"].present is True
    assert report.runtime["pydantic_core"].binaries == ()
    assert report.base["pydantic_core"].binaries  # the fallback is viable

    strict = sa.decide_precedence(settings, report)
    assert strict.ok is False
    assert strict.effective == "runtime-first"

    degraded = sa.decide_precedence(
        replace(settings, allow_degraded_base_first=True), report
    )
    assert degraded.ok is True
    assert degraded.effective == "base-first"
    assert degraded.degraded is True


# ---------------------------------------------------------------------------
# Config shape
# ---------------------------------------------------------------------------


def test_config_reads_mode_and_sidecar_section(tmp_path):
    config = tmp_path / "rt_toggle.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "default": "rt_sidecar",
                "targets": {
                    "rt_sidecar": {
                        "home": "runtime",
                        "python_version": "3.14",
                        "site_packages": "runtime/Lib/site-packages",
                        "mode": "sidecar",
                        "sidecar": {
                            "instance": "brain",
                            "launcher": "python314.exe",
                            "host_script": "host_stub.py",
                            "package_precedence": "runtime-first",
                            "allow_degraded_base_first": True,
                            "health_timeout": 2.5,
                            "critical_packages": ["pydantic_core"],
                        },
                    },
                    "in_process": {
                        "home": "runtime",
                        "python_version": "3.14",
                        "site_packages": "runtime/Lib/site-packages",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    mode, settings = sa.sidecar_settings_from_config(config, "rt_sidecar")
    assert mode == "sidecar"
    assert settings is not None
    assert settings.instance == "brain"
    assert settings.package_precedence == "runtime-first"
    assert settings.allow_degraded_base_first is True
    assert settings.health_timeout == 2.5
    assert settings.launcher == (tmp_path / "python314.exe").resolve()
    assert settings.host_script == (tmp_path / "host_stub.py").resolve()
    assert settings.runtime_home == (tmp_path / "runtime").resolve()
    assert settings.site_packages == (tmp_path / "runtime" / "Lib" / "site-packages").resolve()

    mode2, settings2 = sa.sidecar_settings_from_config(config, "in_process")
    assert mode2 == "in_process"
    assert settings2 is None


def test_config_rejects_an_unknown_mode(tmp_path):
    config = tmp_path / "rt_toggle.json"
    config.write_text(
        json.dumps(
            {
                "targets": {
                    "bad": {
                        "home": "runtime",
                        "python_version": "3.14",
                        "site_packages": "runtime/Lib/site-packages",
                        "mode": "out_of_process",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(sa.SidecarConfigError):
        sa.sidecar_settings_from_config(config, "bad")


def test_in_process_targets_default_to_in_process_mode():
    assert sa.target_mode({"site_packages": "x"}) == "in_process"
    assert sa.target_mode({"mode": "sidecar"}) == "sidecar"


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def test_probe_reports_mode_launcher_precedence_and_integrity(tmp_path):
    supervisor = FakeSupervisor(report=ready_report())
    adapter = make_adapter(make_settings(tmp_path), supervisor)

    probe = adapter.probe()

    assert probe["mode"] == "sidecar"
    assert probe["target"] == "houdini_rt"
    assert probe["launcher"].endswith("python314.exe")
    assert probe["launcher_exists"] is True
    assert probe["launcher_python"] == "3.14"
    assert probe["expect_python"] == "3.14"
    assert probe["python_314"] is True
    assert probe["package_precedence"] == "runtime-first"
    assert probe["precedence_ok"] is True
    assert probe["precedence_degraded"] is False
    assert probe["integrity"]["ok"] is True
    assert probe["integrity"]["blockers"] == []
    assert probe["integrity"]["runtime_tree"]["python_exe"] is False  # not repaired
    assert probe["runtime_state"] == "on"
    assert probe["active"] is True
    assert probe["port"] == 55555
    # The in-process probe's key shape is preserved for a panel.
    for key in ("available", "python_version", "site_packages_exists", "compatibility", "active"):
        assert key in probe


# ---------------------------------------------------------------------------
# In-process path unchanged + no hou
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_in_process_adapter_is_unchanged_mock_level(tmp_path, monkeypatch):
    """The existing in-process path still appends and removes exactly one path."""
    if not (REPO_ROOT / "rt_runtime" / "adapter.py").is_file():
        pytest.skip("repo checkout is not present on this machine")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from rt_runtime import adapter as adapter_mod

    site = tmp_path / "site-packages"
    site.mkdir()
    target = adapter_mod.RuntimeTarget(
        name="test-target",
        home=tmp_path,
        python_version="3.14",
        site_packages=site,
    )

    class Report:
        safe = True
        reasons = ["mock: host and target match"]
        blockers = []
        interpreter_abi = "cp314"

    class Compat:
        @staticmethod
        def check_compatibility(target, allow_pure_python_only=False):
            return Report()

    monkeypatch.setattr(adapter_mod, "_load_compat", lambda: Compat)

    adapter = adapter_mod.RuntimeAdapter(target, state_path=None)
    assert adapter.status() is adapter_mod.RuntimeState.OFF
    assert adapter_mod._path_index(sys.path, site) < 0

    on = adapter.activate()
    assert on.ok is True
    assert on.state is adapter_mod.RuntimeState.ON
    assert on.changed is True
    assert adapter_mod._path_index(sys.path, site) >= 0
    assert adapter.status() is adapter_mod.RuntimeState.ON

    off = adapter.deactivate()
    assert off.ok is True
    assert off.state is adapter_mod.RuntimeState.OFF
    assert off.changed is True
    assert adapter_mod._path_index(sys.path, site) < 0


def test_sidecar_module_never_imports_hou():
    source = Path(sa.__file__).read_text(encoding="utf-8")
    assert "import hou" not in source
    assert "from hou" not in source
    assert not hasattr(sa, "hou")
    assert "hou" not in sys.modules


def test_sidecar_module_imports_no_third_party_packages():
    import ast

    tree = ast.parse(Path(sa.__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    allowed = {
        "__future__",
        "json",
        "os",
        "re",
        "sys",
        "threading",
        "time",
        "dataclasses",
        "enum",
        "pathlib",
        "typing",
        "importlib",
        "rt_runtime",  # lazy, optional; falls back when absent
    }
    assert roots <= allowed, f"unexpected imports: {sorted(roots - allowed)}"
