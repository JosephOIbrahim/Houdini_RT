# Claims vs Reference — HoudiniRT activation answer

Status: scaffold review. No Houdini session was launched and no claim below was
executed in Houdini. "Verified" means verified against files on disk or against
the local documentation corpus; it does not mean runtime-tested.

This document compares the side-agent answer, as briefed in claims C1-C6, with
the local SideFX reference library at `D:\Houdini_AI_Reference`. The six claim
statements are taken from the task brief; if the verbatim answer differs in
wording, the claim-level verdicts below still apply to the substance.

---

## 1. What the reference library is, and is not

`D:\Houdini_AI_Reference` is a local SideFX documentation corpus plus original
scaffold tooling.

- **Corpus size and scope:** 3,893 scoped pages, 0 pending, 158 upstream 404
  targets. Sections: `hom/` 880, `vex/` 1,109, `solaris/` 65, `copernicus/` 24,
  `nodes/cop/` 364, `nodes/lop/` 189, `nodes/sop/` 1,262
  (`D:\Houdini_AI_Reference\LIBRARY_README.md`).
- **Target build:** 22.0.429. The live index at crawl time identified 22.0.461,
  so the crawl is not an atomic build snapshot
  (`LIBRARY_README.md`).
- **Source of truth:** unchanged upstream bytes live under
  `D:\Houdini_AI_Reference\upstream_sidefx\snapshot-2026-10-04\docs\`.
- **What it does not contain:** no HoudiniRT package, no HoudiniRT activation
  guide, and no RT-specific page. Searches for `houdini_rt`, `HoudiniRT`, and
  `rt_toggle` across the library return nothing.
- **What it also does not contain:** the UI/panel/shelf reference pages. The
  crawl covers HOM, VEX, Solaris, Copernicus and node pages. Python Panel
  Editor, shelf configuration and environment-variable docs are in the
  installed Houdini help zips, not in the D: crawl. Those installed files are
  used below only as corroboration, clearly labeled.

Searches run for this review:

| Pattern | Scope | Result |
|---|---|---|
| `houdini_rt`, `HoudiniRT`, `rt_toggle` | whole `D:\Houdini_AI_Reference` | no hits |
| `pypanel`, `PYTHONPATH` | `upstream_sidefx\snapshot-2026-10-04` | HOM pages only |
| `cameraMenu` / camera menu mutation API | `docs\hom` | no insertion API; only descriptions and existing-entry actions |
| `shelfDocument` | corpus | no hits (shelf format is in installed `shelf.zip`) |

---

## 2. Verdict summary

| Claim | Subject | Verdict | Basis |
|---|---|---|---|
| C1 | HoudiniRT structure | **Supported locally**, not by D: | Direct inspection of `%HOUDINI_RT_ROOT%` |
| C2 | Add whole `site-packages` to `PYTHONPATH` | **Contradicted in practice** | 3.13 host vs 3.14 target, 104 `cp314` binaries |
| C3 | Import in the Houdini Python Shell | **Not addressed** | D: silent; needs a build check |
| C4 | Run `Scripts\*.exe` outside Houdini | **Not addressed; directionally right** | Wrapper shebang verified; end-to-end unverified |
| C5 | Which package is "the RT" is unspecified | **Not addressed; true** | No RT-named package found; no corpus guidance |
| C6 | Don't guess; load the docs skill | **Supported as method guidance** | The local skill and corpus tooling exist |

Count: 1 supported locally, 1 contradicted, 3 not addressed, 1 method-guidance
claim. None of the six is a HoudiniRT fact sourced from D:, because D: has no
HoudiniRT material.

---

## 3. Claim-by-claim

### C1 — Structure

**Claim as briefed:** HoudiniRT is a separate embedded Python distribution with
its own home, `Lib\site-packages`, `libs`, and `Scripts`.

**Verdict: supported locally. Not supported by D:.**

Direct evidence from `%HOUDINI_RT_ROOT%`:

| Observation | Path / value |
|---|---|
| Separate distribution root | `%HOUDINI_RT_ROOT%` |
| 3.14 import library | `libs\python314.lib` |
| 3.14 runtime DLL referenced by the launcher | `pythonw.exe` contains `python314.dll` |
| 3.14 extension modules | 104 `*cp314*.pyd` files under `Lib\site-packages` (175 `.pyd` total) |
| Full package set | 572 entries under `Lib\site-packages` |
| Launcher present | `pythonw.exe` only; no `python.exe` in the root |
| Console wrappers | 102 `.exe` files under `Scripts\` |

`D:\Houdini_AI_Reference` says nothing about any of this. It is a Houdini docs
corpus. C1 is a claim about the E: tree, so the correct source is the E: tree,
and the E: tree supports it.

**Limit:** C1 describes the shape of the runtime. It says nothing about whether
that runtime can be loaded into Houdini. Supporting C1 does not support C2.

### C2 — Add the whole `site-packages` to `PYTHONPATH`

**Claim as briefed:** Add the HoudiniRT `Lib\site-packages` directory to
Houdini's `PYTHONPATH` so Houdini can import the runtime.

**Verdict: contradicted in practice.** The D: corpus does not contain this
claim, and the local build facts contradict it.

Evidence:

| Fact | Value | Source |
|---|---|---|
| Houdini interpreter | Python 3.13.10 | `D:\Houdini_AI_Reference\runtime_smoke_evidence.json` (`python_version`) |
| Houdini runtime DLL | `python313.dll` | `C:\Program Files\Side Effects Software\Houdini 22.0.429\bin\` |
| Houdini extension ABI | `PyImath_Python3_13-3_2.dll` | same folder |
| HoudiniRT interpreter | Python 3.14 | `libs\python314.lib`; `pythonw.exe` → `python314.dll` |
| HoudiniRT compiled modules | 104 `cp314` `.pyd` files | `Lib\site-packages` |
| Other 3.14 binaries | 102 `Scripts\*.exe` wrappers | `Scripts\` |

Why the claim fails:

1. Python extension modules are ABI-tagged. A `cp314` `.pyd` is built against the
   3.14 C ABI and cannot be imported by a 3.13 interpreter. The failure is at
   import time at best, and can be process-level at worst. The exact symptom is
   unmeasured here, which is why the design treats it as unsafe rather than
   merely "slow".
2. Adding the directory to `PYTHONPATH` makes all 175 `.pyd` files and all 572
   packages visible. Houdini bundles its own `numpy`, Qt bindings and other
   modules. A second copy on `sys.path` can shadow or conflict with Houdini's
   own imports even when the RT module itself is never imported.
3. `PYTHONPATH` is the wrong lever at runtime. CPython reads `PYTHONPATH` while
   building `sys.path` at startup. Setting `os.environ["PYTHONPATH"]` inside a
   running Houdini does not rebuild `sys.path`; the adapter must append to
   `sys.path` directly. The docs describe `PYTHONPATH` for `$HFS/houdini/
   pythonX.Ylibs` (`docs\hom\commandline.md:7`; `docs\hom\locations.md:181`),
   which is a same-ABI case, not an arbitrary distribution.

What D: actually supports: only that `PYTHONPATH` is a documented way to make
Houdini's own matching-ABI Python modules importable. It does not support adding
a 3.14 tree.

**Correction:** see Section 4.

### C3 — Import in the Houdini Python Shell

**Claim as briefed:** The runtime can be imported from the Houdini Python Shell.

**Verdict: not addressed by D:.** Needs a build check.

What the corpus does say:

- `docs\hom\commandline.md:3` documents the reverse direction: importing `hou`
  into a regular Python shell, after adding `$HFS/houdini/pythonX.Ylibs` to
  `PYTHONPATH` or `sys.path`.
- `docs\hom\cb\*.md` shows the Python Shell (Windows ▸ Python Shell) being used
  for Houdini API snippets.
- Neither page discusses importing a package built for a different Python
  version into Houdini's interpreter.

Why it matters: a Houdini Python Shell import runs under 3.13.10. Any RT module
that imports a `cp314` dependency fails. A module that is genuinely pure Python
may import, but may still call 3.14-only standard-library APIs.

Build check that would settle it (do not add the path permanently):

1. In Houdini's Python Shell, run `import sys; print(sys.version)` and confirm
   3.13.10.
2. Append a single RT package directory to `sys.path` for the session only.
3. Import a module known to be pure Python and record the result.
4. Import a module with a `cp314` dependency and record whether the failure is a
   clean `ImportError` or something worse.
5. Restart Houdini; do not save the path anywhere.

**Verdict stands: not addressed, not disproven.**

### C4 — Run `Scripts\*.exe` outside Houdini

**Claim as briefed:** Run the runtime's `Scripts\*.exe` wrappers outside Houdini
instead of loading it into Houdini.

**Verdict: not addressed by D:; directionally right; end-to-end unverified.**

Local evidence:

- `Scripts\pytest.exe` embeds the shebang `#!C:\Python314\python.exe`.
- `C:\Python314\python.exe` reports
  `3.14.2 (tags/v3.14.2:df79316, Dec 5 2025) [MSC v.1944 64 bit (AMD64)]`.
- There are 102 such wrappers.

Why "directionally right": the wrappers are console launchers for a 3.14
interpreter, so running them is an outside-Houdini 3.14 execution path. That is
the correct side of the ABI boundary.

Why it is not proven:

- The shebang points at `C:\Python314\python.exe`, the base 3.14 install, not at
  the HoudiniRT `pythonw.exe`. Whether a given wrapper uses HoudiniRT's
  `Lib\site-packages` depends on how that interpreter's `sys.path` is configured.
  It may not exercise the RT tree at all.
- The wrapper may expect its package to be installed in `C:\Python314`, not in
  the RT distribution. That is a per-wrapper check, not a general guarantee.
- No wrapper was run for this review.

Build check: run a harmless wrapper such as `Scripts\pip.exe --version` with the
RT environment, then print `sys.executable` and `sys.path` from a 3.14 one-liner
launched the same way. That shows which interpreter and which site-packages are
actually in play.

D: says nothing about this. The claim is local-tooling reasoning, not a
documented SideFX behavior.

### C5 — Which package is "the RT" is unspecified

**Claim as briefed:** The answer does not say which package or module is the
runtime; "the RT" is left unspecified.

**Verdict: not addressed by D:, and true of the local tree.**

Evidence:

- No `houdini_rt`, `HoudiniRT`, `rt`, or `rt_toggle` package exists under
  `%HOUDINI_RT_ROOT%\Lib\site-packages`.
- The tree has 572 entries. Houdini-adjacent candidates include
  `synapse_houdini` (editable install pointing at `%USERPROFILE%\SYNAPSE\python`),
  `usd_substrate`, `usd_composition_search`, `cognitive_orchestra`, `orchestra`,
  `otto_os`, `departures`, and `hanish`. None is named as the runtime by any
  local manifest.
- D: has no package list for HoudiniRT and cannot resolve the question.

Consequences for the design:

- `entry_point` is `null` in `rt_toggle.json`. Activation is path exposure only;
  it does not import or verify a named runtime.
- The toggle can honestly say "the target's paths are visible". It cannot say
  "the RT is running" until a package and an entry point are chosen.
- Choosing the package is a product decision, not a documentation lookup.

### C6 — Don't guess; load the docs skill

**Claim as briefed:** Do not answer Houdini specifics from memory; load the local
docs skill and cite the corpus.

**Verdict: supported as method guidance.**

This is a process claim, and the local tooling supports it:

- The user-level `myth-logic-houdini` skill exists for exactly this: look up
  Houdini 22 node names, VEX signatures, `hou` calls and defaults in the local
  SideFX docs and installed help before stating them.
- `D:\Houdini_AI_Reference\corpus_search_v3.py` is the bounded, hash-checked
  citation tool described in `LIBRARY_README.md`. Example from the README:
  `python D:\Houdini_AI_Reference\corpus_search_v3.py createNode --section hom/ --source hom/hou/Node.md --limit 5`.

Limit: method guidance does not resolve C2 or C5. It tells you where to look; it
does not supply an RT activation guide, because none exists in the corpus.

Tooling note from this review: running `corpus_search_v3.py` with Python's `-I`
flag fails with `ModuleNotFoundError: markdown_links`, because `-I` removes the
script directory from `sys.path`. Run it as the README shows, from its own
directory, or add that directory to `PYTHONPATH`.

---

## 4. Corrected C2

The following replaces C2 in full.

> **Do not add `%HOUDINI_RT_ROOT%\Lib\site-packages` to
> Houdini's `PYTHONPATH`, to `sys.path`, to a Houdini package JSON, to
> `houdini.env`, or to a startup script.**
>
> Houdini 22.0.429 embeds Python 3.13.10 (`bin\python313.dll`). The HoudiniRT
> `site-packages` directory is a Python 3.14 tree: it contains 104 `cp314`
> extension modules and 175 `.pyd` files in total. Python 3.13 cannot import
> `cp314` binaries, and exposing the whole tree can shadow Houdini's own bundled
> modules.
>
> **What to add instead:** only the extension root
> `%HOUDINI_RT_ROOT%\extensions\rt_toggle`, which contains
> the pure-Python `rt_runtime` package. That is what
> `houdini\packages\rt_toggle.json` appends.
>
> **What gates the runtime:** the `rt_runtime` compatibility guard. Under a 3.13
> host it must refuse the full 3.14 `site-packages`. It may allow a curated
> pure-Python subset, with an explicit allowlist and a warning, only when no
> ABI-specific binaries are present.
>
> **How to execute 3.14 work:** launch it outside Houdini's interpreter, via
> `C:\Python314\python.exe` (3.14.2) or the `Scripts\*.exe` wrappers whose
> shebang points there. That is a subprocess model, not an in-process toggle.
>
> **When the full directory is legitimate:** only under a 3.14 host whose ABI
> matches the tree, for example a process launched with `C:\Python314\python.exe`
> and configured to use that `site-packages`. Even then, verify the import list;
> ABI match is necessary, not sufficient.

Why this correction is not optional:

1. The original claim crosses an ABI boundary that Houdini cannot cross.
2. It is a workstation-wide change if written into `PYTHONPATH` or
   `houdini.env`; it would affect every Houdini session, not just this toggle.
3. It makes the failure mode a process-level import failure instead of a clean
   refusal from the guard.

Implementation note (outside Houdini): `rt_runtime\compat.py` now implements
this refusal by scanning filenames only. Run under `C:\Python314\python.exe`
with a simulated host version, `check_compatibility(target,
houdini_python="3.13.10")` returns `safe=False` with an `interpreter_abi` of
`cp313` and a blocker naming 104 `cp314` files. The same call under 3.14.2
returns `safe=True`. The guard has not run inside Houdini's own interpreter.

---

## 5. What only a build check can settle

These remain open after the corpus review:

1. The exact failure of importing a `cp314` `.pyd` under Houdini's 3.13
   interpreter: clean `ImportError`, a `SystemError`, or a crash.
2. Whether a curated pure-Python subset from the RT tree imports cleanly under
   3.13, including transitive dependencies.
3. Whether `Scripts\*.exe` wrappers use `C:\Python314`'s site-packages or the RT
   tree's `Lib\site-packages`.
4. Which package is the runtime, and whether it has an importable entry point.
5. Whether the panel's `.pypanel` installs and renders in 22.0.429.
6. Whether the shelf file loads from `HOUDINI_TOOLBAR_PATH` and the pane menu
   supplement merges as intended.

---

## 6. Citation index

Local build facts:

- `D:\Houdini_AI_Reference\runtime_smoke_evidence.json` — `python_version`
  `3.13.10`, build `22.0.429`.
- `D:\Houdini_AI_Reference\LIBRARY_README.md` — 3,893 pages, section counts,
  target build 22.0.429, live index 22.0.461.
- `C:\Program Files\Side Effects Software\Houdini 22.0.429\bin\python313.dll` —
  Houdini interpreter ABI.
- `%HOUDINI_RT_ROOT%\libs\python314.lib` — RT import library.
- `%HOUDINI_RT_ROOT%\Lib\site-packages` — 104 `cp314` `.pyd`,
  175 `.pyd` total, 572 entries.
- `%HOUDINI_RT_ROOT%\Scripts\pytest.exe` — embedded shebang
  `#!C:\Python314\python.exe`.
- `C:\Python314\python.exe` — reports 3.14.2.

Corpus pages:

- `D:\Houdini_AI_Reference\upstream_sidefx\snapshot-2026-10-04\docs\hom\commandline.md:3,7`
  — importing `hou` from a regular Python shell; `PYTHONPATH` for
  `$HFS/houdini/pythonX.Ylibs`.
- `...\docs\hom\locations.md:181` — `PYTHONPATH` or `sys.path` for packages in
  other locations.
- `...\docs\hom\hou\pypanel.md:7,14` — `installFile`, `interfaceByName`.
- `...\docs\hom\hou\PythonPanel.md:34` — `setActiveInterface`.
- `...\docs\hom\hou\Pane.md:33` — `createTab`.
- `...\docs\hom\hou\paneTabType.md:51` — `PythonPanel`.
- `...\docs\hom\hou\GeometryViewport.md:87,145` — camera menu described, not
  extensible.
- `...\docs\hom\hou\SceneViewer.md:847` — action equivalent to an existing
  camera-menu item.
- `...\docs\hom\hou\ViewerStateMenu.md` — context menus for viewer states only.

Installed help (corroboration for surfaces, not for RT):

- `C:\Program Files\Side Effects Software\Houdini 22.0.429\houdini\help\shelf.zip`,
  `config_file.txt` — shelf XML and `HOUDINI_TOOLBAR_PATH`.
- `...\help\ref.zip`, `env.txt` — `HOUDINI_PYTHON_PANEL_PATH`,
  `HOUDINI_TOOLBAR_PATH`.
- `...\help\ref.zip`, `windows\pythonpaneleditor.txt` — `.pypanel` startup
  search paths and pane menu insertion.
- `...\houdini\PaneTabTypeMenu.xml:38-43` — `pythonpanel::<interface>` insertion.
