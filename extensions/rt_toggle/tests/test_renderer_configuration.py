"""Release contract for Houdini's interpretation of native depth/AOV buffers."""
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = "houdini/UsdRenderers.json"


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_houdini_depth_configuration_is_scoped_to_the_native_plugin():
    # SideFX HDK documents opengl as [0, 1]; normalized defaults to [-1, 1].
    config = json.loads((ROOT / CONFIG).read_text(encoding="utf-8"))
    assert config == {"HdRTRendererPlugin": {"depthstyle": "opengl", "aovsupport": True}}


def test_renderer_configuration_is_required_and_collected_for_release():
    builder = load_tool("build_release")
    entries = {member.as_posix(): path for member, path in builder.collect_entries()}
    assert entries[CONFIG] == ROOT / CONFIG
    assert CONFIG in builder.REQUIRED_FILES
    assert CONFIG in load_tool("audit_release").REQUIRED_FILES
