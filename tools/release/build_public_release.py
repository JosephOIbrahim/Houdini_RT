"""Package verified installed bytes; never install, publish or modify the source payload."""
import argparse
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

VERSION = "0.5.0"
BUILD = "22.0.429"
TESTED_COMMIT = "87bff34e87d242d59a30fd6f222e625445a66976"
PREFIX = "packages/HoudiniRT-" + VERSION
ARCHIVE = "HoudiniRT-0.5.0-houdini22.0.429-windows-x64.zip"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LICENSES = {
    "LICENSE": "LICENSE",
    PREFIX + "/licenses/Apache-2.0.txt": "LICENSE",
    PREFIX + "/licenses/Renderer-NOTICE.md": "src/hdRT/NOTICE.md",
    PREFIX + "/licenses/Pixar-TOST-LICENSE.txt": "third_party/openusd_hdEmbree/LICENSE.txt",
    PREFIX + "/licenses/Sascha-Willems-MIT-LICENSE.md": "third_party/sascha_willems_vulkan/LICENSE.md",
    PREFIX + "/licenses/OpenPBR-LICENSE.txt": "third_party/openpbr-bsdf/LICENSE",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()


def relative(name):
    if (not isinstance(name, str) or "\\" in name or ":" in name
            or any(ord(c) < 32 for c in name)):
        raise ValueError("Unsafe relative path: " + repr(name))
    path = PurePosixPath(name)
    if not name or name == "." or path.is_absolute() or path.as_posix() != name or ".." in path.parts:
        raise ValueError("Unsafe relative path: " + repr(name))
    return path


def read_inside(root, name):
    path = root.joinpath(*relative(name).parts)
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("Path escapes input root: " + name)
    return path.read_bytes()


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Expected SHA-256")
    return value


def git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments], stderr=subprocess.PIPE)


def git_files(root, revision, names):
    """Read exact Git objects once; do not trust mutable working files as provenance."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("An exact 40-character commit is required")
    if git(root, "rev-parse", revision + "^{commit}").decode().strip() != revision:
        raise ValueError("Revision is not the supplied commit")
    ordered = sorted(set(names))
    queries = "".join(revision + ":" + str(relative(n)) + "\n" for n in ordered)
    result = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"],
                            input=queries.encode(), stdout=subprocess.PIPE, check=True)
    stream = io.BytesIO(result.stdout)
    output = {}
    for name in ordered:
        header = stream.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError("Missing committed file: " + name)
        size = int(header[2]); data = stream.read(size)
        if len(data) != size or stream.read(1) != b"\n":
            raise ValueError("Invalid Git object response")
        output[name] = data
    return output


def is_runtime(name):
    path = relative(name)
    if any(p in ("__pycache__", ".pytest_cache") for p in path.parts):
        return False
    if path.suffix in (".pyc", ".pyo"):
        return False
    if name in ("extension/VERSION", "extension/rt_toggle.json", "extension/LICENSE"):
        return True
    if name.startswith("extension/houdini/"):
        return "packages" not in path.parts and path.suffix.lower() != ".md"
    return name.startswith(("renderer/", "extension/rt_runtime/", "houdini/"))


def verify_sources(root, manifest, tested_commit):
    sources = manifest["source_files"]
    native = manifest["native_source_files"]
    if not sources or not native:
        raise ValueError("Empty source inventory")
    for name, expected in sources.items():
        relative(name); digest(expected)
        if not name.startswith(("src/hdRT/", "third_party/openpbr-bsdf/", "extensions/rt_toggle/")):
            raise ValueError("Unexpected source namespace: " + name)
    if {n: h for n, h in sources.items() if not n.startswith("extensions/")} != native:
        raise ValueError("Native and complete source inventories disagree")
    committed = git_files(root, tested_commit, sources)
    for name, expected in sources.items():
        if sha(committed[name]) != expected or sha(read_inside(root, name)) != expected:
            raise ValueError("Tested source mismatch: " + name)
    return dict(native), {n: h for n, h in sources.items() if n.startswith("extensions/")}


def verify_payload(installation, manifest):
    files = manifest["files"]
    if not files:
        raise ValueError("Empty installed inventory")
    selected = {}
    for name, expected in files.items():
        data = read_inside(installation, name)
        if sha(data) != digest(expected):
            raise ValueError("Installed payload mismatch: " + name)
        if is_runtime(name):
            selected[name] = data
    actual = {p.relative_to(installation).as_posix() for p in installation.rglob("*")
              if p.is_file() and is_runtime(p.relative_to(installation).as_posix())}
    if actual != set(selected):
        raise ValueError("Unmanifested runtime files: " + repr(sorted(actual ^ set(selected))))
    required = {"renderer/hdRT.dll", "renderer/plugInfo.json", "renderer/capabilities.json",
                "renderer/shaders/furnace.spv", "renderer/shaders/resolve.spv",
                "extension/VERSION", "extension/rt_toggle.json", "extension/LICENSE",
                "extension/rt_runtime/render_session.py", "houdini/UsdRenderers.json",
                "houdini/python_panels/rt_panel.pypanel", "houdini/toolbar/rt_shelf.shelf"}
    if not required <= set(selected):
        raise ValueError("Required runtime files missing")
    if selected["extension/VERSION"].decode().strip() != VERSION:
        raise ValueError("Wrong extension version")
    if json.loads(selected["renderer/capabilities.json"])["profile_version"] != "textures-hdri-v1":
        raise ValueError("Wrong native capability profile")
    return selected


def evidence(manifest, extra):
    proofs = dict(manifest["proofs"])
    for argument in extra:
        name, separator, path = argument.partition("=")
        if not separator or not re.fullmatch(r"[a-z0-9_]+", name) or name in proofs:
            raise ValueError("Evidence must be a unique name=path")
        proofs[name] = {"path": path, "sha256": sha(Path(path).read_bytes())}
    if not proofs:
        raise ValueError("No acceptance evidence")
    result = {}
    for name, entry in proofs.items():
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise ValueError("Invalid evidence label")
        data = Path(entry["path"]).read_bytes()
        if sha(data) != digest(entry["sha256"]) or json.loads(data).get("status") != "passed":
            raise ValueError("Unverified evidence: " + name)
        result[name] = sha(data)
    return result


def write_zip(output, files):
    if len({name.casefold() for name in files}) != len(files):
        raise ValueError("Archive paths collide on Windows")
    output.mkdir(parents=True, exist_ok=False)
    archive = output / ARCHIVE
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as z:
        for name in sorted(files):
            relative(name)
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 17, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, files[name])
    with zipfile.ZipFile(archive) as z:
        if z.namelist() != sorted(files) or any(z.read(n) != data for n, data in files.items()):
            raise ValueError("Archive readback mismatch")
    (output / "SHA256SUMS").write_bytes((sha(archive.read_bytes()) + "  " + ARCHIVE + "\n").encode())
    return archive


def build(args):
    root = ROOT.resolve()
    installation = args.installation.resolve()
    if args.tested_code_commit != TESTED_COMMIT:
        raise ValueError("This release requires the accepted tested code commit")
    manifest_bytes = args.manifest.read_bytes()
    if sha(manifest_bytes) != digest(args.manifest_sha256):
        raise ValueError("Installation manifest hash mismatch")
    if args.manifest.resolve() != installation / "install_manifest.json":
        raise ValueError("Manifest must belong to the supplied installation")
    manifest = json.loads(manifest_bytes)
    if (manifest.get("product"), manifest.get("extension_version"), manifest.get("houdini_build")) != ("HoudiniRT", VERSION, BUILD):
        raise ValueError("Wrong product, version or Houdini build")
    if Path(manifest["installation_root"]).resolve() != installation:
        raise ValueError("Installation identity mismatch")
    native, extension = verify_sources(root, manifest, args.tested_code_commit)
    runtime = verify_payload(installation, manifest)
    proof_hashes = evidence(manifest, args.evidence)
    old_package = Path(manifest["package_path"]).read_bytes()
    if sha(old_package) != manifest["package_sha256"]:
        raise ValueError("Installed discovery package changed")
    portable_bytes = (HERE / "portable_package.json").read_bytes()
    portable = json.loads(portable_bytes)
    expected_package = json.loads(old_package)
    expected_package["env"][0] = {"HOUDINIRT_HOME": "$HOUDINI_PACKAGE_PATH/HoudiniRT-" + VERSION}
    if portable != expected_package:
        raise ValueError("Portable settings differ from tested native package")
    public_paths = [args.readme.resolve(), args.release_notes.resolve()]
    tool_paths = [p for p in HERE.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    release_inputs = {}
    for path in public_paths + tool_paths:
        name = path.relative_to(root).as_posix()
        release_inputs[name] = read_inside(root, name)
    if release_inputs[(HERE / "portable_package.json").relative_to(root).as_posix()] != portable_bytes:
        raise ValueError("Package template changed while preparing release")
    if args.provisional:
        if args.release_revision:
            raise ValueError("Provisional builds must not claim a release revision")
    else:
        if not args.release_revision:
            raise ValueError("Final release requires --release-revision")
        if git(root, "rev-parse", "HEAD").decode().strip() != args.release_revision:
            raise ValueError("Final release revision must be checked out as HEAD")
        if git(root, "status", "--porcelain", "--untracked-files=all").strip():
            raise ValueError("Final release requires a clean working tree")
        git(root, "merge-base", "--is-ancestor", args.tested_code_commit, args.release_revision)
        committed = git_files(root, args.release_revision, set(release_inputs) | set(manifest["source_files"]))
        if any(committed[n] != data for n, data in release_inputs.items()):
            raise ValueError("Release documentation/tooling differs from release revision")
        if any(sha(committed[n]) != h for n, h in manifest["source_files"].items()):
            raise ValueError("Release revision contains different native or extension sources")
    license_inputs = git_files(root, args.tested_code_commit, LICENSES.values())
    for name, data in license_inputs.items():
        if read_inside(root, name) != data:
            raise ValueError("License or notice differs from tested revision: " + name)
    files = {PREFIX + "/" + name: data for name, data in runtime.items()}
    files.update({name: license_inputs[source] for name, source in LICENSES.items()})
    files["README.md"] = release_inputs[public_paths[0].relative_to(root).as_posix()]
    files[PREFIX + "/RELEASE_NOTES.md"] = release_inputs[public_paths[1].relative_to(root).as_posix()]
    files["packages/zz_houdinirt.json"] = encode(portable)
    public_manifest = {
        "schema_version": 1, "product": "HoudiniRT", "version": VERSION,
        "channel": "public-preview", "houdini_build": BUILD, "platform": "windows-x64",
        "provisional": args.provisional, "tested_code_commit": args.tested_code_commit,
        "release_revision": args.release_revision, "installation_manifest_sha256": sha(manifest_bytes),
        "native_source_files": native, "extension_source_files": extension,
        "release_inputs": {n: sha(b) for n, b in release_inputs.items()},
        "evidence_receipt_sha256": proof_hashes,
        "files": {name: sha(data) for name, data in files.items()},
        "hash_scope": "Archive-relative file paths; this manifest excludes its own hash. SHA256SUMS hashes the complete archive.",
    }
    files[PREFIX + "/release_manifest.json"] = encode(public_manifest)
    output = args.output.resolve()
    if not output.is_relative_to(root / "build/public-release") or output == root / "build/public-release":
        raise ValueError("Output must be a new directory below build/public-release")
    archive = write_zip(output, files)
    print(json.dumps({"status": "built", "provisional": args.provisional,
                      "archive": str(archive), "sha256": sha(archive.read_bytes()), "files": len(files)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installation", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--tested-code-commit", required=True)
    parser.add_argument("--release-revision")
    parser.add_argument("--provisional", action="store_true")
    parser.add_argument("--readme", type=Path, required=True)
    parser.add_argument("--release-notes", type=Path, required=True)
    parser.add_argument("--evidence", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
