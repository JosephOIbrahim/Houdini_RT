# RT Toggle v0.1.0

**A scaffold release, not a Houdini-tested release.** No Houdini 22.0.429
session and no hython session was launched. The code and files are here; the
Houdini behaviour is not proven.

## What this is

An ON/OFF control for the HoudiniRT runtime, packaged as a Houdini extension.
The extension lives at:

```
%HOUDINI_RT_ROOT%\extensions\rt_toggle
```

The controllable runtime is Python 3.14. Houdini 22.0.429 embeds Python 3.13.10.
That difference is the central fact of this release: the whole HoudiniRT
`site-packages` tree cannot be loaded into Houdini, so the toggle ships a
compatibility guard that can refuse activation cleanly.

## The three surfaces

1. **Python Panel** — `houdini/rt_panel.pypanel`, interface `RT_Toggle`. Shows
   the current state, the target, and the compatibility verdict. A pane tab type
   menu supplement (`houdini/PaneTabTypeMenu.xml`) adds it to the pane menu.
2. **Shelf tool** — `houdini/rt_shelf.shelf` plus `houdini/rt_shelf.py`. Shelf
   set `RT`, tab `RT`, tool `rt_toggle`. One click, one status line.
3. **Node recipe** — `houdini/rt_node.py` plus `houdini/RT_TOGGLE_NODE.md`. An
   `enable` toggle whose parm callback writes the result into `rt_status`.

All three call the same `rt_runtime.session.get_adapter()` object, so they share
one lock, one `sys.path` record, and one state file.

## ABI policy: strict by default, opt-in only

The guard compares the host interpreter with the target's declared interpreter
and scans extension filenames for `cp313` / `cp314` / `abi3` tags. It never
imports the modules it inspects.

- **Default:** a version mismatch is refused. The shipped `houdini_rt` target
  (Python 3.14, 104 `cp314` extension modules) is refused on Houdini 22.0.429's
  Python 3.13.10 host.
- **Opt-in:** a target may set `allow_pure_python_only: true`. That permits a
  mismatched tree only when it contains no compiled extensions. It is read from
  the target config and forwarded through the adapter.
- **Hard refusal:** a compiled extension whose filename tag conflicts with the
  host is refused even if the target's declared version matches. The declared
  version is a claim; the filenames are evidence.

"ON" currently means the target's `site-packages` path is on `sys.path`. It does
not mean a named runtime package was imported, because `entry_point` is `null`.

## What is proven

- **74 pytest tests pass locally** on CPython 3.12.10 (run 2026-11-11), with no
  Houdini and no GUI. They cover adapter activation/deactivation, idempotency,
  exact `sys.path` restoration, environment apply/restore, the state-file
  schema, compatibility refusals and allowances, and the UI file contracts.
- **File validation:** the suite parses and asserts the `.pypanel` and `.shelf`
  XML, checks the node recipe's entry point and callback registration, loads
  runtime targets from config mappings, and validates the state-file JSON
  schema. The shipped package JSON, runtime config, and pane menu supplement are
  valid on inspection but are not parsed by the suite.
- **The release tree can be validated structurally:** `tools/build_release.py`
  checks required files and rejects caches, bytecode, and unsafe archive paths.
  The tool exists; the release checklist item that runs it is marked NOT RUN.

These results prove the tree is internally consistent on plain CPython. They do
not prove any Houdini behaviour.

## What is not proven

- No activation, refusal, or toggle has run inside Houdini 22.0.429 or hython.
- The panel has not rendered under Houdini's PySide6.
- The shelf has not loaded from `HOUDINI_TOOLBAR_PATH`.
- The pane menu supplement has not merged in the target build.
- The node parm callback has not fired in Houdini.
- The guard's exact refusal text on 22.0.429 is unmeasured.
- Importing a `cp314` `.pyd` under Houdini's 3.13 interpreter has not been
  observed, so the failure mode (clean `ImportError` versus process failure) is
  still unknown.

## Install

Read `INSTALL.md`. It is a six-step install with a hard warning against adding
`%HOUDINI_RT_ROOT%\Lib\site-packages` to `PYTHONPATH`,
`houdini.env`, a package file, or a startup script. Only the extension root is
added to `PYTHONPATH`.

Live checks for a human in Houdini are in `verification/INSTALL_CHECKS.md` and
`verification/RUN.md`; every one is marked NOT RUN.

## Open owner decisions

- **Pure-Python allowlist versus subprocess runtime.** For work that genuinely
  needs Python 3.14, the two candidate paths are a curated, explicitly listed
  pure-Python subset admitted by the guard, or running 3.14 work in a subprocess
  outside Houdini. Neither is implemented. `allow_pure_python_only` exists but
  the shipped target keeps it `false`.
- **Runtime package and entry point.** No package has been chosen as "the RT".
  Until one is, the toggle can report path exposure but not runtime health.

Repository visibility is not open: it has been decided as private.
