"""Profile provenance, bounded iteration and report lifecycle without Houdini."""
import json
from types import SimpleNamespace as NS
from concurrent.futures import ThreadPoolExecutor

import pytest

from rt_runtime.scene_support import CapabilityProfile, SceneSupportScan, load_profile


PROFILE = CapabilityProfile("installed/capabilities.json", "test-v1",
                            frozenset({"domeLight", "distantLight", "rectLight", "diskLight", "sphereLight"}),
                            frozenset({"color", "normal", "depth", "primId"}))


class Attr:
    def __init__(self, name, value=None, interpolation=None, connections=()):
        self.name, self.value = name, value
        self.interpolation, self.connections = interpolation, connections
    def Get(self, time): return self.value
    def GetName(self): return self.name
    def HasAuthoredValueOpinion(self): return True
    def GetMetadata(self, key): return self.interpolation
    def GetConnections(self): return self.connections
    def GetNumTimeSamples(self): return 0
    def GetPropertyStack(self, time): return [NS(HasDefaultValue=lambda: True, default=self.value)]


class Prim:
    def __init__(self, path, kind="Xform", attrs=(), rels=(), loaded=True, active=True, instance=False):
        self.path, self.kind = path, kind
        self.attrs = {a.name: a for a in attrs}
        self.rels, self.loaded, self.active, self.instance = rels, loaded, active, instance
        self.matrix = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    def GetPath(self): return self.path
    def GetTypeName(self): return self.kind
    def IsActive(self): return self.active
    def IsLoaded(self): return self.loaded
    def IsInstance(self): return self.instance
    def GetAttribute(self, name): return self.attrs.get(name)
    def GetAttributes(self): return tuple(self.attrs.values())
    def GetRelationships(self): return self.rels
    def GetAppliedSchemas(self): return ()


class Stage:
    def __init__(self, prims): self.prims, self.reads = prims, 0
    def TraverseAll(self):
        for prim in self.prims:
            self.reads += 1
            yield prim


class Notice:
    handles = []
    @classmethod
    def Register(cls, kind, callback, stage):
        handle = NS(callback=callback, stage=stage, revoked=False)
        handle.Revoke = lambda: setattr(handle, "revoked", True)
        cls.handles.append(handle)
        return handle


def make(prims=(), **kwargs):
    stage = Stage(prims)
    viewer = NS(current_stage=stage, locked=False, lop=True)
    viewer.stage = lambda: viewer.current_stage
    viewer.name = lambda: "intended"
    viewer.isViewingSceneGraph = lambda: viewer.lop
    viewer.sceneGraphStageLocked = lambda: viewer.locked
    host = NS(current_frame=1)
    host.frame = lambda: host.current_frame
    usd = NS(TimeCode=lambda frame: frame, Notice=NS(ObjectsChanged=object()))
    geom = NS(Xformable=lambda prim: NS(ComputeLocalToWorldTransform=lambda time: prim.matrix))
    scan = SceneSupportScan(viewer, PROFILE, hou_module=host, usd_modules=(usd, geom, NS(Notice=Notice)), **kwargs)
    return scan, viewer, host, stage


def finish(scan):
    while scan.report.state == "running": scan.step()
    return scan.report


def write_profile(directory, **updates):
    directory.mkdir(parents=True, exist_ok=True)
    data = dict(schema_version=1, renderer="HoudiniRT", profile_version="test-v1",
                supported_lights=["domeLight"], supported_aovs=["color"])
    data.update(updates)
    (directory / "capabilities.json").write_text(json.dumps(data), encoding="utf-8")


def test_profile_uses_explicit_root_and_rejects_missing_instead_of_source_fallback(tmp_path):
    write_profile(tmp_path / "resources")
    profile = load_profile({"HRT_PLUGIN_ROOT": str(tmp_path)})
    assert profile.version == "test-v1" and profile.aovs == {"color"}
    with pytest.raises(ValueError, match="not found"):
        load_profile({"HRT_PLUGIN_ROOT": str(tmp_path / "absent")})


def test_registered_plugin_profile_precedes_home_fallback(tmp_path):
    write_profile(tmp_path / "plugin", profile_version="registered")
    write_profile(tmp_path / "home" / "renderer", profile_version="other")
    registry = NS(GetPluginWithName=lambda name: NS(resourcePath=str(tmp_path / "plugin")))
    assert load_profile({"HOUDINIRT_HOME": str(tmp_path / "home")}, registry).version == "registered"


def test_registered_plugin_without_profile_cannot_borrow_newer_home_profile(tmp_path):
    write_profile(tmp_path / "home" / "renderer", profile_version="newer")
    registry = NS(GetPluginWithName=lambda name: NS(resourcePath=str(tmp_path / "old-plugin")))
    with pytest.raises(ValueError, match="not found"):
        load_profile({"HOUDINIRT_HOME": str(tmp_path / "home")}, registry)


def test_profile_limitations_disclose_sentinel_outputs_and_build(tmp_path):
    from rt_runtime.scene_support import SupportReport
    write_profile(tmp_path, build_id="tested-h22.0.429", supported_aovs=["instanceId"],
                  limitations=["instanceId is -1; instancing is unsupported"])
    report = SupportReport(profile=load_profile({"HRT_PLUGIN_ROOT": str(tmp_path)}))
    assert "tested-h22.0.429" in report.text()
    assert "instanceId is -1" in report.text()


@pytest.mark.parametrize("update", [{"schema_version": 2}, {"renderer": "Other"},
                                    {"supported_aovs": "color"}, {"supported_lights": [3]}])
def test_invalid_profile_fails_closed(tmp_path, update):
    write_profile(tmp_path, **update)
    with pytest.raises(ValueError): load_profile({"HRT_PLUGIN_ROOT": str(tmp_path)})


def test_scan_is_incremental_and_cancel_releases_notice_without_loading_more_prims():
    scan, viewer, host, stage = make([Prim("/p%d" % i) for i in range(300)])
    scan.step(max_items=7)
    assert stage.reads == 7 and scan.report.state == "running"
    handle = Notice.handles[-1]
    scan.cancel()
    scan.step()
    assert stage.reads == 7 and scan.report.state == "partial" and handle.revoked


def test_time_budget_stops_before_count_limit():
    ticks = iter([0.0, 0.010, 0.020])
    scan, _, _, stage = make([Prim("/a"), Prim("/b")], clock=lambda: next(ticks))
    scan.step(budget_seconds=0.008)
    assert stage.reads == 1 and scan.report.state == "running"
    scan.close()


@pytest.mark.parametrize("change", ["notice", "stage", "frame", "viewer", "locked", "context"])
def test_snapshot_invalidates_before_more_reads_and_after_completion(change):
    scan, viewer, host, stage = make([Prim("/a")])
    finish(scan)
    handle = Notice.handles[-1]
    target = viewer
    if change == "notice": handle.callback(None, stage)
    elif change == "stage": viewer.current_stage = Stage([])
    elif change == "frame": host.current_frame = 2
    elif change == "viewer": target = object()
    elif change == "locked": viewer.locked = True
    elif change == "context": viewer.lop = False
    scan.check_current(target)
    assert scan.report.state == "stale" and handle.revoked and stage.reads == 1


def test_scan_limits_and_unloaded_content_are_explicit():
    scan, _, _, _ = make([Prim("/payload", loaded=False), Prim("/next"), Prim("/other")], max_prims=2)
    report = finish(scan)
    assert report.state == "partial" and report.inspected == 2 and report.skipped == 1
    assert report.findings[0].path == "/payload"
    assert report.findings[0].kind == "Not assessed"
    assert "remaining" in report.detail


def test_report_keeps_exact_paths_and_does_not_claim_analytic_geometry_unsupported():
    scan, _, _, _ = make([Prim("/model/sphere", "Sphere"), Prim("/lights/tube", "CylinderLight"),
                          Prim("/instanced", instance=True)])
    report = finish(scan)
    assert [(f.path, f.kind) for f in report.findings] == [
        ("/model/sphere", "Not assessed"), ("/lights/tube", "Unsupported"), ("/instanced", "Unsupported")]
    assert "not a native translated-scene census" in report.text()
    scan.close()


def test_supported_lights_allow_native_properties_but_flag_ignored_features():
    supported = Prim("/key", "RectLight", [Attr("inputs:intensity", 4), Attr("inputs:normalize", True),
                     Attr("inputs:enableColorTemperature", True), Attr("inputs:width", 3)])
    shaped = Prim("/bad", "DomeLight", [Attr("inputs:texture:file", NS(path="sky.exr")),
                  Attr("inputs:shaping:focus", 1), Attr("collection:lightLink:includeRoot", False),
                  Attr("inputs:shadow:enable", False)])
    scan, _, _, _ = make([supported, shaped])
    report = finish(scan)
    unsupported = [f for f in report.findings if f.kind == "Unsupported"]
    assert all(f.path == "/bad" for f in unsupported)
    assert {f.feature for f in unsupported} == {
        "inputs:texture:file", "inputs:shaping:focus", "lightLink", "inputs:shadow:enable"}
    scan.close()


def test_sphere_transform_checks_parent_world_scale_and_shear():
    sphere = Prim("/sphere", "SphereLight", [Attr("treatAsPoint", True)])
    sphere.matrix[0][0] = 2
    scan, _, _, _ = make([sphere])
    assert {f.feature for f in finish(scan).findings} == {
        "Finite light geometry", "treatAsPoint", "Sphere transform"}
    scan.close()


def test_finite_light_geometry_limitation_names_each_path_and_ignored_karma_switches():
    types = ("RectLight", "DiskLight", "SphereLight", "DistantLight", "DomeLight")
    scan, _, _, _ = make([Prim("/rig/" + kind, kind) for kind in types])
    findings = finish(scan).findings
    assert {f.path for f in findings} == {"/rig/" + kind for kind in types[:3]}
    for finding in findings:
        assert finding.kind == "Limitation" and finding.feature == "Finite light geometry"
        assert "visible opaque emitter" in finding.reason
        assert "renderlightgeo" in finding.reason and "lightgeocastsshadow" in finding.reason
        assert "not evaluated" in finding.reason
    scan.close()


def test_mesh_reductions_and_render_var_requests_are_not_silently_accepted():
    material = NS(GetName=lambda: "material:binding", GetTargets=lambda: ["/material"])
    mesh = Prim("/mesh", "Mesh", [Attr("primvars:displayColor", interpolation="vertex")], [material])
    aov = Prim("/vars/cryptomatte", "RenderVar", [Attr("sourceName", "crypto"), Attr("sourceType", "raw")])
    scan, _, _, _ = make([mesh, aov], max_findings=3)
    report = finish(scan)
    assert report.omitted_findings == 1
    assert {f.feature for f in report.findings} == {"Material binding", "Subdivision catmullClark", "displayColor vertex"}
    assert "additional findings omitted" in report.text()
    scan.close()


def test_worker_thread_cannot_touch_viewer_or_usd():
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(SceneSupportScan, object())
        with pytest.raises(RuntimeError, match="main thread"): future.result()


def test_winning_normals_primvar_does_not_report_unused_intrinsic_opinion():
    mesh = Prim("/mesh", "Mesh", [Attr("subdivisionScheme", "none"),
                Attr("normals", interpolation="invalid-unused"),
                Attr("primvars:normals", interpolation="faceVarying")])
    scan, _, _, _ = make([mesh])
    assert not finish(scan).findings
    scan.close()
