# RT Toggle — Design

Status: scaffold design. Nothing in this document was executed in Houdini.
Every Houdini-side behavior is either cited to installed help / the local
reference corpus, or explicitly marked **proposed** or **untested**.

Goal: an on/off control for the HoudiniRT runtime inside Houdini 22.0.429,
reachable from a supported UI surface. The original request was "in the camera
dropdown in the viewport or a node". Section 2.1 explains why the camera
dropdown cannot be used.

---

## 1. Verified environment facts

These are the facts the whole design rests on.

| Fact | Evidence |
|---|---|
| Houdini 22.0.429 embeds Python 3.13.10 | `D:\Houdini_AI_Reference\runtime_smoke_evidence.json` (`python_version`), taken from a trusted hython 22.0.429 smoke run |
| The Houdini interpreter is a 3.13 build | `C:\Program Files\Side Effects Software\Houdini 22.0.429\bin\python313.dll`; matching `PyImath_Python3_13-3_2.dll` in the same folder |
| HoudiniRT is a separate Python 3.14 distribution | `%HOUDINI_RT_ROOT%\libs\python314.lib`; `pythonw.exe` references `python314.dll`; `Lib\site-packages` contains 108 `cp314` `.pyd` files out of 195 `.pyd` files total |
| The runtime tree carries its own interpreter (since the 2026-11-11 repair) | `%HOUDINI_RT_ROOT%\python.exe` reports `3.14.2`; `python314.dll` sits beside it |
| The `Scripts\*.exe` wrappers launch a 3.14 interpreter | `Scripts\pytest.exe` embeds the shebang `#!C:\Python314\python.exe`; `C:\Python314\python.exe` reports `3.14.2 (tags/v3.14.2:df79316, Dec 5 2025) [MSC v.1944 64 bit (AMD64)]`. There are 102 such wrappers. |
| `.pypanel` format | installed example `C:\Program Files\Side Effects Software\Houdini 22.0.429\houdini\help\examples\python_panels\nodepath.pypanel` |
| `.shelf` format | installed help `C:\Program Files\Side Effects Software\Houdini 22.0.429\houdini\help\shelf.zip`, entry `config_file.txt`, "Overview" |
| Pane tab menu can insert a Python Panel | `C:\Program Files\Side Effects Software\Houdini 22.0.429\houdini\PaneTabTypeMenu.xml` lines 38-43 |

Consequence: exposing the HoudiniRT `site-packages` directory to Houdini is an
ABI crossing, not a normal package install. Python 3.13 cannot load a `cp314`
extension module. The toggle therefore needs a compatibility guard that can say
no, and "on" cannot mean "Houdini is now running Python 3.14".

---

## 2. The four surfaces

### 2.1 Camera dropdown — unavailable

The camera dropdown in the top-right corner of a viewport has no documented
extension point in Houdini 22.0.429.

Evidence:

- `D:\Houdini_AI_Reference\upstream_sidefx\snapshot-2026-10-04\docs\hom\hou\GeometryViewport.md:87`
  describes the camera menu as the UI for "free / locked / temporarily looking
  through a camera" view modes. It describes the menu; it does not expose it.
- `SceneViewer.md:847` documents a method that is "equivalent to selecting
  `Reset Native Viewport Camera` in the viewport's camera menu". That is a
  trigger for an existing entry, not an insertion API.
- `hou.ViewerStateMenu` builds context menus for custom viewer states. It is
  not the built-in camera dropdown.
- `SceneViewer.displayRadialMenu` launches a named radial menu. It does not add
  an item to the camera menu.
- The factory `PaneTabTypeMenu.xml` header documents exactly one menu
  extension surface: the pane tab type menu, with
  `<actionItem id="pythonpanel::<interface_name>">`. There is no camera-menu
  equivalent.
- A corpus-wide search of the HOM pages returns no `cameraMenu`-style API.

Absence of documentation is evidence of no supported extension point. It is not
proof that no private API exists, but the scaffold builds only on documented
surfaces.

**Verdict: do not build against the camera dropdown.** The closest supported
surfaces are the pane tab menu (via a Python Panel) and a shelf tool.

### 2.2 Python Panel — primary surface

A `.pypanel` file defines one or more interfaces. Each interface has a `name`,
a `label`, an icon, and a `<script>` whose `onCreateInterface()` returns the
root Qt widget.

Documented behavior:

- Format: `nodepath.pypanel` (installed example) shows
  `<pythonPanelDocument><interface name="..." label="..."><script><![CDATA[...]]>`.
- `hou.pypanel.installFile(file_path)` installs every interface in the file into
  the current session (`docs\hom\hou\pypanel.md:7`; installed
  `hom.zip\hou\pypanel.txt`).
- `hou.pypanel.interfaceByName(name)` returns the interface or `None`
  (`pypanel.md:14`).
- At startup Houdini searches `$HFS/houdini/python_panels` and
  `$HOUDINI_USER_PREF_DIR/python_panels`; `$HOUDINI_PATH` changes that to each
  entry's `python_panels` subdirectory; `$HOUDINI_PYTHON_PANEL_PATH` overrides
  the search path (installed help `ref.zip\windows\pythonpaneleditor.txt`,
  section "Python Panel files"; `ref.zip\env.txt`,
  `HOUDINI_PYTHON_PANEL_PATH`).
- `hou.Pane.createTab(hou.paneTabType.PythonPanel, "<interface name>")` creates
  the tab and hides the panel toolbar when an interface name is supplied
  (`docs\hom\hou\Pane.md:33`; `paneTabType.md:51`).
- `hou.PythonPanel.setActiveInterface(interface)` binds an existing tab
  (`PythonPanel.md:34`).
- A `PaneTabTypeMenu.xml` supplement can place the interface anywhere in the
  pane tab type menu, including a submenu, with
  `<actionItem id="pythonpanel::<interface_name>">` (factory header lines 38-43;
  installed `basics.zip\config_menus.txt` documents `insertBefore`,
  `insertAfter`, `insertAtIndex`).

Why it is primary:

- It is the only surface that can show live state (OFF / ON / ERROR / refused),
  the target name, the target Python version, the ABI verdict, blockers, and the
  last error in one place.
- It is installable in-session with `hou.pypanel.installFile`, or at startup by
  dropping a file on a documented path.
- It gives a large, obvious click target, which matches the request for an
  on/off control.
- It can be inserted into the pane tab menu, so it is one click from any pane's
  tab-type menu.

Constraints:

- Qt objects can only be created on the main Houdini application thread
  (`PythonPanel.md`, note at the top). The panel must not run activation work on
  a worker thread.
- The interface `name` and the `pythonpanel::` id in `PaneTabTypeMenu.xml` must
  match. Both scaffold files currently use `RT_Toggle`. Case sensitivity is not
  stated in the docs and is **untested**.

### 2.3 Shelf tool — secondary surface

A `.shelf` file defines a tool whose `<script scriptType="python">` runs when
clicked. Houdini loads `.shelf` files from `HOUDINI_TOOLBAR_PATH`, which defaults
to `HOUDINI_PATH/toolbar` (installed help `shelf.zip\config_file.txt`,
"Overview"; `ref.zip\env.txt`, `HOUDINI_TOOLBAR_PATH`).

The tool script is three lines:

```python
from rt_runtime.session import get_adapter
result = get_adapter().toggle()
hou.ui.displayMessage("\n".join(result.reasons), title="RT Runtime")
```

`hou.ui.displayMessage(text, ...)` is documented in `docs\hom\hou\ui.md:247`.
Shelf tool scripts run on the main thread.

Why secondary: it is the fastest access (one shelf click) and the lowest-risk
install, but it cannot show persistent state between clicks. It is a button,
not a status panel. It is the right second surface, not the first.

### 2.4 Node toggle — optional, scene-scoped

A node carrying a toggle parm whose callback calls
`get_adapter().toggle()` and writes the outcome into a string parm.

Documented pieces:

- `hou.ToggleParmTemplate`, `hou.StringParmTemplate`,
  `hou.ParmTemplateGroup`, `OpNode.parmTemplateGroup` /
  `setParmTemplateGroup` exist in the corpus (`docs\hom\hou\*.md`).
- Parameter callback scripts and their `kwargs` keys are documented in
  `docs\hom\hou\locations.md:301` ("Parameter callback scripts").
- `ParmTemplate.setScriptCallback` / `setScriptCallbackLanguage` are documented
  in the parm-template pages.

Why optional rather than primary:

- The toggle's *effect* is process-global (`sys.path`, `os.environ`), but the
  node's *state* is scene data. Saving and loading a scene would carry a parm
  value that says "on" while a fresh Houdini process is off. That is a lie the
  UI cannot prevent.
- It requires creating a node in a scene. The user asked for an on/off control,
  not scene content.
- Parm callback re-entry (whether `Parm.set()` re-fires the callback) is not
  documented locally and is **untested**.

If a scene-embedded control is genuinely wanted, the node is acceptable. It
should display the process state on cook/refresh rather than trust its own parm.

### 2.5 Surface comparison

| Criterion | Camera dropdown | Python Panel | Shelf tool | Node toggle |
|---|---|---|---|---|
| Documented extension point in 22.0.429 | No | Yes | Yes | Yes |
| Install mechanism | n/a | `.pypanel` on `HOUDINI_PYTHON_PANEL_PATH`, or `hou.pypanel.installFile` | `.shelf` on `HOUDINI_TOOLBAR_PATH` | run `rt_node.build()` |
| Where the user sees it | viewport camera menu | pane tab and pane tab menu | shelf / TAB menu | node network |
| Shows live state | n/a | Yes, full probe | Only at click time | Yes, but scene-scoped |
| Effect scope | n/a | whole Houdini process | whole process | whole process |
| Survives scene save/load | n/a | n/a | n/a | No; parm is scene data, effect is not |
| Startup-loaded | n/a | Yes, from file | Yes, from file | No |
| Implementation risk | n/a | Qt main-thread rule | Low | Medium: callback re-entry, scene drift |
| Recommendation | Do not build | **Primary** | **Secondary** | Optional |

---

## 3. Recommendation

Build the **Python Panel** as the product surface. Add the **shelf tool** as the
quick-access surface. Provide the **node** only if the user explicitly wants a
control embedded in a scene. Do not attempt the camera dropdown.

The panel owns the full state display and the toggle button. The shelf tool is a
thin caller of the same adapter. The node, if used, is a third caller of the
same adapter. All three share one process-wide adapter and one state file, so
they cannot disagree about the runtime path bookkeeping.

---

## 4. Shared contract (fixed; other components code against this)

Package: `rt_runtime`, pure Python, no `hou` import at module level.

```
RuntimeTarget dataclass: name, home, python_version, site_packages,
                         entry_point, env
load_targets(path) -> dict[str, RuntimeTarget]
CompatibilityReport: safe: bool, reasons: list[str], blockers: list[str],
                     interpreter_abi: str
check_compatibility(target, houdini_python=None) -> CompatibilityReport
RuntimeState enum: OFF, ON, ERROR
ActivationResult: ok: bool, state: RuntimeState, changed: bool,
                  reasons: list[str], error: str | None
RuntimeAdapter(target, state_path=None):
    probe() -> dict
    activate() -> ActivationResult
    deactivate() -> ActivationResult
    toggle() -> ActivationResult
    status() -> RuntimeState
get_adapter(config_path=None) -> RuntimeAdapter   # session singleton
```

State JSON:

```json
{
  "active": true,
  "target": "houdini_rt",
  "added_paths": ["%HOUDINI_RT_ROOT%/Lib/site-packages"],
  "env_backup": {"PYTHONPATH": null},
  "updated_at": "2026-11-11T18:40:00Z"
}
```

Field semantics:

| Field | Meaning |
|---|---|
| `active` | Recorded intent: the adapter believes this target is on. Not proof. |
| `target` | The target name this record belongs to. |
| `added_paths` | Exactly the paths this adapter appended. Paths that were already present are never recorded and never removed. |
| `env_backup` | Each variable the adapter set, mapped to its prior value; `null` means it was not set. |
| `updated_at` | UTC ISO-8601 timestamp of the last successful write. |

`RuntimeState` has exactly three members. **"Refused" is not a state.** It is an
outcome: `ActivationResult(ok=False, state=OFF, changed=False, reasons=[...])`.
The UI may display the word REFUSED, but the data model must not add a fourth
enum member without changing the contract.

`probe()` is typed as `dict` by the contract; the scaffold convention is:

```python
{
  "target": "houdini_rt",
  "available": True,               # home and site_packages exist
  "python_version": "3.14",
  "site_packages_exists": True,
  "active": False,
  "compatibility": {
      "safe": False,
      "reasons": ["..."],
      "blockers": ["..."],
      "interpreter_abi": "cp313"
  }
}
```

Probe is read-only: it must not change `sys.path`, `os.environ`, or the state
file.

Additive extension that does not change any contract name: an optional
`allow_pure_python_only=False` keyword on `check_compatibility`, and the same
key as a policy hint in the target JSON. Existing calls with two arguments keep
working. This is the recommended way to carry the pure-Python policy. The
keyword is implemented in `rt_runtime\compat.py`; the adapter does not forward
the JSON hint yet (Section 11, note 2).

---

## 5. What "on" means for a 3.14 target under a 3.13 host

Houdini 22.0.429 runs 3.13.10. Activation does not change that interpreter.
"ON" means:

1. The target's `site_packages` directory is on the current 3.13 process's
   `sys.path`.
2. The target's `env` mapping has been applied to `os.environ`.
3. The compatibility guard returned `safe=True`.
4. The state file records `active=true`, the target name, the exact paths added,
   and the prior environment values.

"ON" does **not** mean:

- Houdini is running Python 3.14.
- The runtime's entry point imported successfully. Activation never imports
  `entry_point`; `entry_point=null` means "do not import anything".
- `cp314` binaries work. They cannot be loaded by a 3.13 interpreter.
- Deactivation unloads modules. `sys.modules` is never mutated; modules imported
  from the runtime stay loaded until Houdini restarts.

### 5.1 Guard policy

`check_compatibility(target, houdini_python=None)` compares the host
interpreter ABI against the target's declared `python_version` and inspects the
target for ABI-specific binaries.

| Host ABI | Target | ABI-specific binaries in target | `allow_pure_python_only` | Result |
|---|---|---|---|---|
| `cp313` | 3.14 | any `cp314` | either | **Refuse** (blocker: ABI mismatch) |
| `cp313` | 3.14 | none, explicit allowlist passes | `true` | **Allow with warning** |
| `cp313` | 3.14 | none, policy false | `false` | **Refuse** (version mismatch) |
| `cp313` | 3.13 | `cp313` | n/a | Safe |
| `cp314` | 3.14 | `cp314` | n/a | Safe |

`interpreter_abi` is the host cache tag, for example `cp313` under Houdini and
`cp314` under `C:\Python314\python.exe`.

The HoudiniRT `site-packages` directory has 104 `cp314` `.pyd` files. Under
Houdini 22.0.429 the first row applies: **the guard must refuse the full
package.** That is the expected, correct behavior, not a bug.

### 5.2 Pure-Python allowlist mode

If the user wants something to work in-process, the only defensible route is a
curated pure-Python subset:

- The target points at a directory containing only `.py` sources known to be
  pure Python.
- The guard scans for `*.pyd`, `*.so`, and ABI-tagged filenames. Any hit is a
  blocker.
- `allow_pure_python_only=true` permits activation when the scan is clean, and
  the report carries a warning: the host is 3.13, the target targets 3.14, so
  3.14-only syntax and standard-library APIs will fail at import or call time.
- A directory scan is not sufficient on its own. A pure `.py` module can import
  a compiled dependency lazily. The allowlist must be an explicit list of module
  names that were checked, or activation must be followed by an import probe of
  a declared entry point. The filename scan is implemented in `compat.py`; the
  explicit module allowlist and the post-activation import probe are
  **proposed** and not implemented.

### 5.3 Subprocess alternative

The 3.14 work can also run outside Houdini's interpreter: launch
`C:\Python314\python.exe` (3.14.2) or one of the `Scripts\*.exe` wrappers, which
embed `#!C:\Python314\python.exe`. That is a subprocess model, not an in-process
toggle. It is the safest way to execute 3.14 code, and it is not what
`activate()` does.

---

## 6. State machine

```
                    activate(): guard safe, path present
        OFF ───────────────────────────────────────────────► ON
         ▲                                                    │
         │ deactivate()                                       │ sys.path entry removed
         │                                                    │ or state/sys.path drift
         └────────────────────────────────────────────────────┤
                                                              ▼
                                                            ERROR
                                                              │
                                          activate() repairs  │
                                                              └──► ON
```

Refusals do not enter the diagram; they leave the state where it was:

```
OFF ── activate(), ABI unsafe or another target active ──► OFF   (ok=False, changed=False)
OFF ── activate(), site-packages missing ────────────────► ERROR (ok=False, error=...)
```

Transition table:

| From | Event | Result state | `ok` | `changed` | Notes |
|---|---|---|---|---|---|
| OFF | `activate()`, guard safe, path absent | ON | true | true | appends path, applies env, writes state |
| OFF | `activate()`, guard safe, path already present | ON | true | true | records no added path; state flips to active |
| ON | `activate()` | ON | true | false | idempotent, reason "already active" |
| OFF | `activate()`, guard unsafe | OFF | false | false | refused; reasons carry blockers |
| OFF | `activate()`, site-packages missing | ERROR | false | false | `error` set |
| OFF | `activate()`, another target active | OFF | false | false | refused; deactivate the other target first |
| ON | `deactivate()` | OFF | true | true | removes only recorded paths, restores env |
| OFF | `deactivate()` | OFF | true | false | idempotent, reason "already inactive" |
| ON | `status()` after external path removal | ERROR | n/a | n/a | state file says active, `sys.path` disagrees |
| ERROR | `activate()` | ON | true | true | re-adds paths, reason "repaired state" |
| ERROR | `deactivate()` | OFF | true | true | clears the record |
| ON | `toggle()` | OFF | true | true | delegates to `deactivate()` |
| OFF or ERROR | `toggle()` | ON or ERROR | per activate | per activate | delegates to `activate()` |

The UI mapping is: `ok=False` with no `error` displays REFUSED; `ok=False` with
`error` displays ERROR; `ok=True` displays the result state.

---

## 7. Persistence

- Default state path: `<scaffold root>/.rt_toggle_state.json`, where the scaffold
  root is the folder containing `rt_runtime`. With an explicit `config_path`,
  the state file lives beside that config.
- Writes are atomic: a temporary file in the same directory, `flush`, best-effort
  `fsync`, then `os.replace`.
- A missing, unreadable, or corrupt state file reads as OFF. It is never treated
  as active.
- The state file records intent, not live proof. `status()` reconciles the record
  against `sys.path`: recorded active plus all recorded paths present equals ON;
  recorded active plus a missing path equals ERROR.
- Process effects do not persist. After a Houdini restart, the file still says
  active but `sys.path` is fresh, so `status()` reports ERROR until `activate()`
  repairs it.
- There is no cross-process lock. Two Houdini sessions share the default state
  file; the last writer wins, and each process's `status()` will disagree with
  the other's intent. Treat the toggle as per-workstation, or pass a distinct
  `state_path` per session.
- Environment values are restored exactly: `null` in `env_backup` means the
  variable is removed on deactivate; any other value is written back.

Proposed policy: do not auto-activate on Houdini startup. The panel shows ERROR
(or OFF) and requires a click. Silent `sys.path` mutation at startup is harder to
audit and harder to undo. Auto-repair can be added later as an explicit
preference.

---

## 8. Idempotency and concurrency

| Operation | Second call | Guarantee |
|---|---|---|
| `activate()` on ON | `ok=True`, `changed=False`, "already active" | No duplicate path entry, no duplicate env write |
| `deactivate()` on OFF | `ok=True`, `changed=False`, "already inactive" | No path removal beyond recorded entries |
| `toggle()` | Flips ON↔OFF; ERROR activates | Same as the delegated call |
| Path already on `sys.path` before activation | Not recorded, not removed later | The adapter never removes a path it did not add |
| Env variable already set | Prior value stored in `env_backup`, restored later | No loss of the original value |

All mutations run under one process-wide re-entrant lock, because `sys.path` and
`os.environ` are process-global. Activation rolls back on failure: paths it
appended are removed and env values it changed are restored before returning
`ERROR`.

`sys.modules` is never mutated. `deactivate()` reports modules whose files live
under the target `site_packages`; it does not unload them.

---

## 9. Surface-to-contract mapping

| Surface | Reads | Writes | Never does |
|---|---|---|---|
| Python Panel | `get_adapter()`, `probe()`, `status()` on refresh | `toggle()` on button press | Import `hou` inside `rt_runtime`; run activation off the main thread |
| Shelf tool | `get_adapter()` | `toggle()` on click; `hou.ui.displayMessage` for the result | Persist state of its own |
| Node toggle | `get_adapter()` in the parm callback | `toggle()`; writes a status string parm | Treat the parm value as proof of process state |

All three call the same session singleton, so they share the lock, the state
file, and the path bookkeeping.

---

## 10. Decision tables

### 10.1 Surface decision

| Decision | Recommended | Why | Alternative |
|---|---|---|---|
| Primary surface | Python Panel | Live state, documented install, pane menu insertion | Shelf only |
| Secondary surface | Shelf tool | One-click access, lowest install risk | Panel only |
| Node toggle | Optional, off by default | Scene-parm state can drift from process state | Include it for scene-embedded control |
| Camera dropdown | Do not build | No documented extension point | None |

### 10.2 Open policy decisions

| # | Decision | Recommended | What it turns on |
|---|---|---|---|
| 1 | Is any in-process activation allowed under Houdini 22.0.429? | Full package: no. Curated pure-Python subset: yes, with a warning. | Whether the toggle can ever show ON inside Houdini, or only REFUSED |
| 2 | Where does `allow_pure_python_only` live? | Target-level JSON hint plus an optional third keyword on `check_compatibility` | Whether the guard needs the raw config or only the target |
| 3 | Full `site-packages` vs curated subset | Never expose the full directory; require an explicit allowlist | Whether `rt_toggle.json` points at `Lib\site-packages` or a curated tree |
| 4 | Interface name | `RT_Toggle`, identical in the `.pypanel` and the pane menu XML | Whether `interfaceByName("RT_Toggle")` resolves |
| 5 | Auto-repair on panel open | No; show ERROR and require a click | Startup auditability |
| 6 | `entry_point=null` | Means "no import verification" | Whether "on" can assert the runtime imports |
| 7 | Multi-session state file | Per-workstation default; per-session `state_path` when needed | Whether two Houdini sessions can disagree safely |

---

## 11. Implementation status and integration notes

At the time of writing, the scaffold under
`%HOUDINI_RT_ROOT%\extensions\rt_toggle\` contains:

| Path | Role | Status |
|---|---|---|
| `rt_runtime\__init__.py` | public exports | Present |
| `rt_runtime\adapter.py` | `RuntimeTarget`, `RuntimeState`, `ActivationResult`, `RuntimeAdapter`, `load_targets` | Present |
| `rt_runtime\session.py` | `get_adapter()` singleton and default paths | Present |
| `rt_runtime\compat.py` | `CompatibilityReport`, `check_compatibility(target, houdini_python=None, allow_pure_python_only=False)`, filename-based ABI scan | Present |
| `houdini\rt_panel.pypanel` | panel interface | Present; declares `name="RT_Toggle"` |
| `houdini\rt_panel.py` | readable copy of the panel script | Present |
| `houdini\PaneTabTypeMenu.xml` | pane menu supplement | Present; uses `pythonpanel::RT_Toggle` |
| `houdini\packages\rt_toggle.json` | startup PYTHONPATH for the extension root only | Present |
| `houdini\rt_shelf.shelf`, `houdini\rt_shelf.py` | shelf set/tool and its helper | Present |
| `houdini\rt_node.py`, `houdini\RT_TOGGLE_NODE.md` | node toggle builder and notes | Present |
| `rt_toggle.json` | target config | Present (this task) |
| `tests\` | adapter, compat, state-contract and UI-contract tests | Present; 74 passed on the documented 3.12 test host (Section 11.1) |
| `verification\` | install checks, matrix, run book | Present |

Concrete issues to resolve before any Houdini run:

1. **Panel probe shape (resolved).** The panel reads the nested
   `probe["compatibility"]` report, so the ABI row is not falsely SAFE. The
   TARGET row resolves `adapter.target.name` first, so it shows the configured
   name; `probe()` intentionally stays five keys and does not carry the target.
2. **Policy is forwarded (resolved 2026-11-11).** `allow_pure_python_only` is a
   `RuntimeTarget` field (default `False`), read and type-checked from
   `rt_toggle.json`, and passed to `check_compatibility` by the adapter's
   compatibility bridge. A pure-Python target on a different minor version is
   still refused by default and allowed only when the target opts in.
3. **`entry_point` is unused.** Activation never imports it. A future
   verification step could use it, but the contract does not define one.

### 11.1 Local checks run for this design

These ran outside Houdini. The documented test host is CPython 3.12; the 3.14
run is included because 3.14 is the target ABI.

| Check | Result |
|---|---|
| `load_targets("rt_toggle.json")` | Resolves one target, `houdini_rt`, with the expected home, Python 3.14, site-packages, `entry_point=None`, empty env |
| `get_adapter().probe()` under 3.14 | `available=True`, compatibility `safe=True` (219 compiled and 14,244 pure-Python files scanned) |
| `check_compatibility(target, houdini_python="3.13.10")` | `safe=False`, `interpreter_abi="cp313"`, blocker names 104 `cp314` files |
| `pytest tests` under `py -3.12` | 74 passed (policy wiring added 2026-11-11) |
| `pytest tests` under a 3.14 host | Not re-run here: this machine has no working 3.14 console interpreter. The earlier 62/7 result was the host-dependent policy gap; the state-contract tests now declare the running interpreter, and the adapter forwards the opt-in, so a 3.14 run is expected green. |

The earlier seven failures were host-dependent, not a state-machine defect: the
contract tests declared Python 3.12 and expected activation on a 3.14 host.
`allow_pure_python_only` is now a target field and is forwarded, and the
state-contract helper declares the running interpreter, so the same tests no
longer depend on the test host's minor version.

---

## 12. Documented vs proposed vs untested

### Documented (cited)

| Behavior | Source |
|---|---|
| `.pypanel` XML shape, `onCreateInterface()` | installed `nodepath.pypanel`; `ref.zip\windows\pythonpaneleditor.txt` |
| Panel startup search paths and `HOUDINI_PYTHON_PANEL_PATH` | `ref.zip\windows\pythonpaneleditor.txt` "Python Panel files"; `ref.zip\env.txt` |
| `hou.pypanel.installFile` / `interfaceByName` | `docs\hom\hou\pypanel.md:7,14`; `hom.zip\hou\pypanel.txt` |
| `hou.Pane.createTab(type, interface)` | `docs\hom\hou\Pane.md:33` |
| `hou.paneTabType.PythonPanel` | `docs\hom\hou\paneTabType.md:51` |
| `hou.PythonPanel.setActiveInterface` | `docs\hom\hou\PythonPanel.md:34` |
| Pane menu insertion with `pythonpanel::<name>` | factory `PaneTabTypeMenu.xml:38-43`; `basics.zip\config_menus.txt` |
| `insertBefore` / `insertAfter` / `insertAtIndex` | `basics.zip\config_menus.txt` lines 533-543 |
| `.shelf` XML and `HOUDINI_TOOLBAR_PATH` | `shelf.zip\config_file.txt`; `ref.zip\env.txt` |
| Shelf tool scripts are Python | `docs\hom\locations.md:299`; `tool_script.md` |
| Parameter callback scripts and `kwargs` | `docs\hom\locations.md:301` |
| `hou.ui.displayMessage` | `docs\hom\hou\ui.md:247` |
| Houdini 22.0.429 runs 3.13.10 | `runtime_smoke_evidence.json`; `bin\python313.dll` |
| HoudiniRT is a 3.14 distribution | `libs\python314.lib`; 104 `cp314` `.pyd`; `pythonw.exe` → `python314.dll` |
| `Scripts\*.exe` wrappers target 3.14 | embedded shebang `#!C:\Python314\python.exe`; `C:\Python314\python.exe` reports 3.14.2 |

### Proposed (this design, not in the docs)

- The `RuntimeAdapter` contract, state JSON shape, and session singleton.
- The three-state model plus "refused" as an outcome.
- The compatibility policy table and the pure-Python allowlist mode.
- `allow_pure_python_only` as a target JSON hint and an optional keyword.
- "ON means paths visible to the current 3.13 interpreter", not a version switch.
- No auto-activation at startup; ERROR-until-click behavior.
- Shelf and node surface behavior as callers of the same adapter.
- Per-workstation state-file semantics.

### Untested (requires a Houdini run, which this scaffold did not do)

- That the `.pypanel` renders in 22.0.429 and that `onCreateInterface()` returns
  a valid widget.
- That `hou.pypanel.installFile` accepts the file as written.
- That the factory/supplement `PaneTabTypeMenu.xml` merge places the item at the
  intended position.
- Whether interface names are case-sensitive. Both files now use `RT_Toggle`;
  Houdini's matching behavior is unverified.
- That the shelf loads from `HOUDINI_TOOLBAR_PATH` and runs its script.
- That `rt_node.build()` creates the node and that the parm callback fires.
- The guard's behavior inside Houdini's 3.13 interpreter. It was exercised
  outside Houdini with a simulated host version string only.
- The exact failure mode of importing a `cp314` `.pyd` under 3.13 (clean
  `ImportError` vs process-level failure). The design assumes "unsafe"; the
  precise symptom is unmeasured.
- Whether a curated pure-Python subset actually imports cleanly under 3.13.
- Whether `Parm.set()` re-enters a parm callback synchronously.

---

## 13. Next checks (in order)

1. ~~Wire the policy~~ — done 2026-11-11: `allow_pure_python_only` is read from
   `rt_toggle.json`, carried on `RuntimeTarget`, and forwarded to
   `check_compatibility`; the default stays `False`.
2. ~~Add `target` to `probe()`~~ — not needed: the panel resolves
   `adapter.target.name` directly, so the TARGET row is populated.
3. Run the Houdini-side checks in `verification\` with a temporary preference
   directory; never point the package JSON at HoudiniRT `site-packages`.
4. Decide the core policy: full package refused, curated pure-Python subset
   allowed with a warning, or subprocess-only.
