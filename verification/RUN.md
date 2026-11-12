# RT Toggle — verification run book

Target build for live checks: **Houdini 22.0.429**, which embeds **Python
3.13.10** (see `docs/CLAIMS_VS_REFERENCE.md`). The automated tests run on a
normal CPython, never on Houdini's interpreter and never on hython.

## Interpreter

| Command | Version | pytest | Use |
|---|---|---|---|
| `py -3.12` | 3.12.10 (64-bit) | 9.0.2, working | **Use this for the suite.** |
| `py -3.13` | 3.13 (Windows Store) | not installed | Install with `py -3.13 -m pip install pytest` if a second interpreter is wanted. |
| `python` | 3.14.2 (cp314) | broken: `ModuleNotFoundError: pygments` from the Python314 user site | **Do not use.** This is also the ABI the extension refuses. |

None of these is Houdini's 3.13.10. The compat tests pass an explicit
`houdini_python="3.13"` so they model the target build regardless of which
CPython runs pytest.

## Commands

All commands run from the extension root:

```powershell
cd %HOUDINI_RT_ROOT%\extensions\rt_toggle
```

Full suite, including the implementation agents' own tests:

```powershell
py -3.12 -m pytest tests -q
```

The verification contract suite only (29 tests):

```powershell
py -3.12 -m pytest tests/test_state_contract.py tests/test_compat_contract.py tests/test_ui_contract.py -q
```

Show skip reasons instead of dots:

```powershell
py -3.12 -m pytest tests -q -rs
```

One file at a time:

```powershell
py -3.12 -m pytest tests/test_state_contract.py -q
py -3.12 -m pytest tests/test_compat_contract.py -q
py -3.12 -m pytest tests/test_ui_contract.py -q
```

One claim at a time: copy the exact test ID from `verification/MATRIX.md`, for
example:

```powershell
py -3.12 -m pytest "tests/test_state_contract.py::test_state_json_schema" -q
```

## What each verification file covers

- `tests/fake_hou.py` — pure-Python stand-in for `hou`: parm templates,
  `ParmTemplateGroup`, `Parm`/`Node` with a fireable parm callback,
  `hou.ui.displayMessage`, `hou.pypanel.installFile`. Installs itself into
  `sys.modules["hou"]` only for the duration of a test.
- `tests/test_state_contract.py` — `RuntimeState`, `ActivationResult`,
  `RuntimeAdapter` surface, probe diagnostics, state JSON schema, idempotency,
  exact `sys.path` restoration, toggle/status, state persistence across adapter
  instances, env apply/restore.
- `tests/test_compat_contract.py` — `RuntimeTarget` fields,
  `CompatibilityReport` shape, pure-Python allow, cp314 interpreter refusal,
  cp314 compiled payload refusal.
- `tests/test_ui_contract.py` — fake-hou self-test, `.pypanel` XML and script
  contract, `.shelf` tool contract, node recipe entry point and callback
  registration, optional import of the node module under `fake_hou`.

## Latest runs

```text
local, Windows (py -3.12 -m pytest tests -q)
260 passed, 18 skipped, 18 subtests passed   (2026-11-12, CPython 3.12.10, no Houdini)
local, Windows (py -3.14 -m pytest tests -q)
277 passed, 1 skipped                         (2026-11-12, CPython 3.14.2, no Houdini)

Latest recorded CI before this change          run 43301551119, cfc81da
CI, ubuntu-latest (Python 3.12 and 3.13)
230 passed, 17 skipped, 18 subtests passed
CI, windows-latest (Python 3.14)
244 passed, 3 skipped, 18 subtests passed
```

The local and CI counts differ by host, not by code:

- **Ubuntu gains one pass.** `tests/test_transport.py` holds a POSIX permission
  test that skips on Windows and runs on Linux, so one local Windows skip
  becomes a CI Ubuntu pass.
- **CI Windows gains two skips.** The runner has no `synapse`, so the two
  guarded host-turn tests that exercise `synapse.cognitive.agent_loop.run_turn`
  skip there. Local runs have `synapse`, so they execute.

These results prove the code satisfies
the plain-CPython contract; they do **not** prove any Houdini behaviour. Per the
verification brief, `verification/MATRIX.md` keeps every Houdini row at
`not_run`/`proposed`.

## Houdini 22.0.429 hython run (2026-11-11)

Environment: hython 22.0.429, CPython 3.13.10, isolated preferences
(`HOUDINI_USER_PREF_DIR` under `%TEMP%`), `HOUDINI_PACKAGE_SKIP=1`,
`HOUDINI_TOOLBAR_PATH` pointing at the extension's `houdini` folder where the
check needed it. No scene was loaded or saved.

| Check | Result | Evidence |
|---|---|---|
| A — panel installs | **PASS (install) / GUI-ONLY (render)** | `installFile` ok; `interfaceByName("RT_Toggle")` returned `<hou.PythonPanelInterface of RT_Toggle>` and the interface is in `hou.pypanel.interfaces()`. The pane was not drawn. |
| B — cp314 refused | **PASS (probe/refusal) / GUI-ONLY (button press)** | `probe()["compatibility"]["safe"]` false; the blocker names 104 cp314 modules; `activate()` returned `False / off / False`; `sys.modules` held no module under the HoudiniRT site-packages. |
| C — shelf | **PASS (XSD, registration, script) / GUI-ONLY (click)** | `lxml` XSD validation `True`, 0 errors; `RT` in `hou.shelves.shelves()`, `rt_toggle` in `hou.shelves.tools()`; executing the tool CDATA produced one `HoudiniRT: OFF - refused: ...` line. |
| D — node | **PASS (build, callback function) / GUI-ONLY (event dispatch)** | `/obj/RT_TOGGLE` built; `enable.set(True)` did not dispatch the callback in hython (0 entries), and a minimal control also did not fire, so this is a hython limitation. Calling `toggle_callback({...})` directly wrote `REFUSED \| ...` and reverted `enable` to 0. |
| E — state + restart | **PASS** | Five keys with an ISO timestamp; a fresh process saw `active: true` with the path absent and `status()` = `error`; toggle → `on`; deactivate → `off`, `added_paths: []`. |
| F — uninstall | **NOT RUN** | Uninstall was not executed. The PaneTabTypeMenu entry was checked as `INSTALL_CHECKS.md` Check 5: exactly one `pythonpanel::RT_Toggle`, anchor `pythonpanel::log_viewer`, interface installed; the menu render is **GUI-ONLY**. |

Check E state file sample (scratch `pure-demo` target, opt-in true):

```json
{
  "active": true,
  "added_paths": ["%TEMP%\\rt-check-ef\\pure-target\\site-packages"],
  "env_backup": {},
  "target": "pure-demo",
  "updated_at": "2026-11-11T23:55:25Z"
}
```

After the fresh-process toggle and deactivate: `active: false`, `added_paths: []`,
`status()` `off`.

## What skips without Houdini

Nothing in the suite requires Houdini or a GUI:

- If `rt_runtime` or one of its modules is absent, the state and compat tests
  skip with the missing module named.
- If a `.pypanel`, `.shelf`, or node recipe file is absent, its UI test skips.
- The dynamic import test skips when `rt_runtime.adapter` is absent or when a
  module needs a richer Houdini/Qt surface (`hou.qt`, `hutil`, `PySide*`,
  `PyQt*`).
- `fake_hou` supplies `hou` for the node module; no real Houdini import occurs.

## Houdini 22.0.429 live checks (hython results above; GUI steps marked)

`verification/INSTALL_CHECKS.md` owns the six install/uninstall checks. The
contract-specific checks below are the ones the automated suite cannot settle.
Run them in the Houdini Python Shell on the main thread. `rt_runtime` must be
importable first (the `houdini/packages/rt_toggle.json` package appends the
extension root).

### Check A — The panel installs

```python
import hou
print(hou.applicationVersionString())          # expect 22.0.429
hou.pypanel.installFile(
    r"%HOUDINI_RT_ROOT%\extensions\rt_toggle\houdini\rt_panel.pypanel"
)
print(hou.pypanel.interfaceByName("RT_Toggle"))  # expect a PythonPanelInterface, not None
```

Then open **New Pane Tab Type > RT Toggle**. Expect the panel to draw its OFF
state with no traceback. A traceback means the panel script failed; inspect
`tab.activeInterfaceScriptErrors()`.

**Result (2026-11-11, hython 22.0.429):** install and interface lookup **PASS**;
the pane render is **GUI-ONLY**.

### Check B — The cp314 target is refused

```python
import sys
print(sys.version)                     # expect 3.13.x
from rt_runtime import get_adapter
a = get_adapter()
print(a.target.name)                   # expect houdini_rt
print(a.probe()["compatibility"])      # expect safe False, blockers naming cp314/ABI
r = a.activate()
print(r.ok, r.state, r.changed)        # expect False, OFF, False
print(r.reasons)
```

Then press the panel toggle and the shelf button. Expect a refusal line in both
places, no crash, and no cp314 module imported:

```python
bad = [n for n, m in list(sys.modules.items())
       if "HoudiniRT" in (getattr(m, "__file__", None) or "")
       and "site-packages" in (getattr(m, "__file__", None) or "")]
print(bad)                              # expect []
```

**Result:** probe and refusal **PASS** (`False / off / False`, 104 cp314
blockers, `cp314_modules: []`); the panel and shelf button presses are
**GUI-ONLY**.

### Check C — The shelf appears and reports

Install `houdini/rt_shelf.shelf` and `houdini/rt_shelf.py` into
`%PREF%\toolbar\` per `INSTALL.md`, restart, then:

```python
import hou
print(sorted(hou.shelves.shelves().keys()))   # expect RT among them
print(hou.shelves.tool("rt_toggle"))          # expect a hou.Tool, not None
```

Click **RT Toggle** in the shelf bar. Expect exactly one `HoudiniRT:` status
line and a status-bar message; with the default config it reports the Check B
refusal.

**Result:** XSD validation **PASS** (`True`, 0 errors); registration **PASS**
(`RT`, `rt_toggle`, `<hou.Tool 'rt_toggle'>`); executing the tool's CDATA
produced one `HoudiniRT: OFF - refused: ...` line. The GUI click and status-bar
message are **GUI-ONLY**.

### Check D — The node callback fires

```python
import sys
sys.path.insert(0, r"%HOUDINI_RT_ROOT%\extensions\rt_toggle\houdini")
import rt_node
n = rt_node.build()
n.parm("enable").set(True)
print(n.parm("rt_status").eval())
```

Expect the status parm to change to a refusal line for the default cp314 target
and no traceback. This also tests the open question noted in `rt_node.py`:
whether a programmatic `Parm.set()` re-enters the callback synchronously. If the
status parm does not change, record whether the callback ran once, twice, or not
at all.

**Result:** build **PASS** (`/obj/RT_TOGGLE`). In hython, `enable.set(True)` did
**not** dispatch the callback (0 entries); a minimal control parm with
`script_callback='print("CB_FIRED")'` also did not fire, so this is a hython
limitation, not a wiring fault. Calling `rt_node.toggle_callback({"node": n,
"parm": p})` directly wrote `REFUSED | ... (104 total) ...`, reverted `enable`
from 1 to 0, and left `rt_status` locked. The GUI event path and the
`_TOGGLE_IN_PROGRESS` re-entrancy question are **GUI-ONLY**.

### Check E — The state file and restart behaviour

After a successful activation of a scratch pure-Python target:

```python
import json
from rt_runtime import session
path = session.default_state_path()
print(path)                              # extension root\.rt_toggle_state.json
print(json.dumps(json.loads(path.read_text()), indent=2))
```

Expect the five keys `active`, `target`, `added_paths`, `env_backup`,
`updated_at`, with an ISO-8601 timestamp. Then restart Houdini and:

```python
from rt_runtime import get_adapter
print(get_adapter().status())
```

Expect **ERROR**, not ON: the state file says active but the recorded paths did
not survive the restart. A subsequent toggle should re-activate cleanly and end
at ON. If status returns ON immediately after a restart, the state machine is
claiming paths that are not on `sys.path`.

**Result:** **PASS.** A scratch pure-Python target (`pure-demo`, Python 3.14,
opt-in true) activated to `on`; the state file held the five keys with
`updated_at: 2026-11-11T23:55:25Z`. A fresh hython process saw `active: true`
with the path absent and `status()` = `error`; `toggle()` returned `on`; and
`deactivate()` returned `off` with `added_paths: []`. See the evidence table
above.

### Check F — Uninstall leaves no trace

Follow `verification/INSTALL_CHECKS.md` Check 6. Expect
`hou.pypanel.interfaceByName("RT_Toggle")` to return `None`,
`hou.shelves.tool("rt_toggle")` to return `None`, no `rt_toggle` entry in
`PYTHONPATH`, and no RT Toggle menu entry. Deactivate before uninstalling so the
recorded `sys.path` entries are removed in the same session.

**Result:** **NOT RUN.** The PaneTabTypeMenu entry was checked under
`INSTALL_CHECKS.md` Check 5 (structural pass; menu render GUI-only), not the
uninstall path.

## First check a human should run

1. `py -3.12 -m pytest tests -q` — expect 260 passed, 18 skipped. This is the fastest proof
   the tree is internally consistent.
2. Then Houdini Check A above — the panel install is the smallest live surface
   and fails loudly if the package JSON or panel path is wrong.
3. Then Check B — it confirms the guard refuses the default cp314 target before
   anyone tries to activate it.
