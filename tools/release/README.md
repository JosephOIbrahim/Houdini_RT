# Integrated public release builder

`build_public_release.py` creates a portable Windows/Houdini 22.0.429 preview
from the verified 0.5.0 installation. It does not build native code, install,
change preferences, commit, tag, push, or publish. It writes only to a new
directory below ignored `build/public-release/` and refuses overwrites.

Supply the installation's exact manifest hash and tested code commit. Every
manifested installed file must match. Native and extension source hashes must
match both the working files and the tested Git commit. No foreign Python,
Houdini, OIIO, OCIO or Vulkan runtime libraries are redistributed.

The archive contains `packages/zz_houdinirt.json` and the versioned sibling
`packages/HoudiniRT-0.5.0/`, plus root README/LICENSE files. The versioned directory
includes runtime files, release notes, required third-party license texts and a
public `release_manifest.json`. That manifest records only relative file names,
source hashes, commits and evidence hashes; local install manifests and raw
receipts are excluded. The external `SHA256SUMS` hashes the complete ZIP.

The package uses `$HOUDINI_PACKAGE_PATH` as documented by
[SideFX](https://www.sidefx.com/docs/houdini/ref/plugins.html#relative-path-variables).
All tested package settings are preserved except the portable installation root.
Legacy nested package registration, historical extension documentation, tests,
build helpers and Python caches are not shipped.

```powershell
python -B tools/release/build_public_release.py `
  --installation INSTALL_ROOT `
  --manifest INSTALL_ROOT/install_manifest.json `
  --manifest-sha256 MANIFEST_SHA256 `
  --tested-code-commit 87bff34e87d242d59a30fd6f222e625445a66976 `
  --release-revision EXACT_RELEASE_COMMIT `
  --readme packaging/README.md `
  --release-notes docs/releases/v0.5.0.md `
  --output build/public-release/FINAL_NEW_DIRECTORY
```

A final build requires the exact release commit at HEAD, a clean working tree,
and byte-identical committed public docs/tooling. For precommit relocation
testing, replace `--release-revision` with `--provisional`; this explicitly records
`provisional: true` and `release_revision: null`. It still verifies all tested
runtime/source inputs. Do not publish provisional archives.

Optional repeated `--evidence NAME=PATH` arguments bind additional passed JSON
receipt hashes without shipping their contents. The eight pinned installation
proofs are always verified. Extra names must be unique lowercase identifiers.

ZIP entries are sorted, use fixed timestamps/modes and uncompressed storage for
byte reproducibility independent of compression-library revisions. Input bytes
are held after verification, and the finished ZIP is read back before writing
its checksum. CPU-only checks:

```powershell
python -B -m unittest discover -s tools/release -p test_public_release.py -v
```

Independent relocation validation and release approval are separate steps.
