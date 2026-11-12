# GUI checks — turn controls on Houdini 22.0.429

Target build: **Houdini 22.0.429** — the only build with structural evidence
behind it (panel install, shelf XSD, shelf registration, `rt_node.build()`,
sidecar ON with `/health` 200). 22.0.466 and 22.0.492 are a second stop after
this list passes. 22.0.400 is untested; 21.0.773 is a different major line and
out of scope.

These eleven steps cover what no automated test can: the click, the dispatch and
the visible result. The client, poller, shelf XML and parm templates are all
already tested or validated mechanically; nothing here repeats that.

## Result summary

Fill one verdict per step. Allowed values: **PASS**, **FAIL**, **NOT RUN**.
`NOT RUN` is the honest answer when a step could not be exercised; it is never
a pass.

| # | Step | Verdict | Evidence (label text, screenshot name, or error) |
|---|---|---|---|
| 1 | Isolated environment starts with the sidecar test config | | |
| 2 | Four surfaces install | | |
| 3 | Panel draws with the TURN section | | |
| 4 | In-process target shows UNSAFE (guard working) | | |
| 5 | Runtime reaches ON; `/health` 200 | | |
| 6 | Offline turn completes, UI responsive | | |
| 7 | Cancel reaches `cancelled` | | |
| 8 | Shelf tools register, submit and cancel | | |
| 9 | Node parms build, Run Turn and Cancel Turn work | | |
| 10 | HUD handles appear over the viewport (and Vulkan, if enabled) | | |
| 11 | Radial menu builds, checks ON, and runs its three actions | | |

## Setup

### Step 1 — isolated environment starts with the sidecar test config

Use a preferences folder that nothing else touches, so a daily config cannot
explain a result either way. Select `verification/rt_toggle_sidecar.json`
explicitly: the shipped default target is in-process and is designed to refuse
the cp314 tree, so without this override the ON and turn steps cannot pass.

In PowerShell, set `HOUDINI_RT_ROOT` to the external CPython distribution and
`RT_TOGGLE_ROOT` to this extension in the Houdini_RT checkout (replace the
placeholder with your checkout path):

```powershell
$env:HOUDINI_USER_PREF_DIR = "$env:TEMP\rt-gui-014\prefs22.0"
$env:RT_TOGGLE_ROOT = "<Houdini_RT checkout>\extensions\rt_toggle"
$env:RT_TOGGLE_CONFIG = "$env:RT_TOGGLE_ROOT\verification\rt_toggle_sidecar.json"
```

Do **not** set `HOUDINI_PACKAGE_SKIP` for this pass: the package JSON must be
active so `rt_runtime` imports. The override config has two targets: its default
`houdini_rt_sidecar` runs the supervised 3.14 process, while
`houdini_rt_in_process` is retained solely for the Step 4 refusal check.

**Pass when:** Houdini starts with that preferences folder and a Python Shell
can run:

```python
import hou
from rt_runtime import get_adapter
print(hou.applicationVersionString())
print(get_adapter().target.name)
```

Expect `22.0.429` and `houdini_rt_sidecar`. At the end of the pass, close
Houdini and clear the three environment variables so the scratch config and
preferences do not affect normal work.

### Step 2 — four surfaces install

Per `INSTALL.md`:

1. `houdini/rt_panel.pypanel` into `%PREF%\python_panels\`
2. `houdini/rt_shelf.shelf` and `houdini/rt_shelf.py` together into `%PREF%\toolbar\`
3. `houdini/packages/rt_toggle.json` into `%PREF%\packages\`
4. `houdini/PaneTabTypeMenu.xml` into `%PREF%\` (merge, do not overwrite an existing one)

Restart Houdini.

**Pass when:** `hou.pypanel.interfaceByName("RT_Toggle")` resolves and
`hou.shelves.tools()` contains `rt_toggle`, `rt_turn` and `rt_turn_cancel`.

## Panel

### Step 3 — panel draws with the TURN section

Open **New Pane Tab Type → RT Toggle**.

**Pass when:** the panel shows the big **TURN ON** button, the STATUS / TARGET /
PYTHON / ABI rows, and below them a **TURN** section with an empty prompt
field, two buttons reading **Run turn (offline)** and **Cancel turn**, and the
label **(idle)**. TARGET reads `houdini_rt_sidecar`. No traceback in the pane.

If something fails: inspect `tab.activeInterfaceScriptErrors()`.

### Step 4 — in-process target is refused

The sidecar target's ABI row should be safe: its launcher is 3.14. The guard's
refusal applies to the separate in-process target, which must be checked
explicitly in the Python Shell:

```python
from rt_runtime import get_adapter
guard = get_adapter(target_name="houdini_rt_in_process")
probe = guard.probe()
print(probe["compatibility"])
result = guard.activate()
print(result.ok, result.state, result.changed)
```

**Pass when:** `probe["compatibility"]["safe"]` is false with cp314 blockers,
and `activate()` returns `False`, `off`, `False`. The panel continues to target
`houdini_rt_sidecar`. This is the guard working, not a failure of the toggle.

### Step 5 — runtime reaches ON; `/health` 200

Press **TURN ON**. Then, in the Python Shell:

```python
from rt_runtime import get_adapter
d = get_adapter().status_detail(check_health=True)
print(d["runtime_state"], d["health"])
```

**Pass when:** status reads **ON**, `runtime_state` is `on`, and
`health["ok"]` is true with `code` `200`. If it stays OFF, copy the reasons
line before doing anything else.

## Turn control — panel

### Step 6 — offline turn completes, UI responsive

Type `hello` in the prompt field and press **Run turn (offline)**.

**Pass when:** the status line shows `running <id>` promptly, then
`complete: …` when the turn ends; and while it polls, dragging the viewport and
pressing **Refresh** both respond. A frozen Houdini here is the finding this
check exists for.

### Step 7 — cancel reaches `cancelled`

Cancelling needs a turn that lasts. Either set `RT_SIDECAR_LIVE_MODEL=1` before
Houdini starts and use a slow prompt, or catch the offline turn if it is slow
enough on the first attempt.

**Pass when:** pressing **Cancel turn** during `running` shows `cancelling <id>`
and then `cancelled`. If the offline turn always finishes first, record
**NOT RUN** with that reason rather than a pass.

## Shelf

### Step 8 — shelf tools register, submit and cancel

Open the **RT** shelf. Three tools must be present: **RT Toggle**, **RT Turn**,
**RT Turn Cancel**.

1. Click **RT Turn**. An input box appears.
2. Enter a prompt and accept. The status bar shows
   `HoudiniRT turn: complete - …` or
   `HoudiniRT turn: running <id> - use RT Turn Cancel to stop it`.
3. If a turn is running, click **RT Turn Cancel** and expect
   `HoudiniRT turn: cancelling <id>`.

**Pass when:** all three tools appear, submit works, and the status bar carries
one line per action. A click may block for up to two seconds by design —
`RT_TURN_POLL_S`, default 2.0 — and that is the bounded poll, not a hang.

## Node

### Step 9 — node parms build, Run Turn and Cancel Turn work

In the Python Shell:

```python
import sys
sys.path.insert(0, r"%HOUDINI_RT_ROOT%\extensions\rt_toggle\houdini")
import rt_node
n = rt_node.build()
print([t.name() for t in n.parmTemplateGroup().entries()])
```

**Pass when:** the parm list includes `rt_prompt`, `rt_run_turn`,
`rt_cancel_turn` and `rt_turn_status`, and RT Turn Status reads `IDLE`.

Then, in the parameter pane:

1. Type a prompt into **RT Prompt** and press **Run Turn**. Status becomes
   `RUNNING <id>` and then `COMPLETE | …`.
2. During a long turn, press **Cancel Turn**. Status becomes `CANCELLING <id>`.

This is the path hython cannot exercise at all — parm callbacks do not fire
there — so a failure here is the most likely real bug of the nine.

## HUD handles — viewport controls

### Step 10 — HUD handles appear over the viewport

HUD handles are the native way to put controls over the view. The recipe is in
`docs/ARTIST_LAUNCH.md`; the short form:

1. Select the RT_TOGGLE node so its parameters show.
2. Drag `enable` into the viewport; expect a checkbox handle.
3. Drag `rt_run_turn` and `rt_cancel_turn`; expect two button handles.
4. Right-click each and turn on **Persistent**, then **Lock Placement**.

**Pass when:** the three handles draw over the view, stay visible when another
node is selected (persistent), the Run Turn button submits, and the Cancel
button reaches `CANCELLING`. A handle that draws but does nothing is a FAIL —
record which one.

**Vulkan sub-check, optional:** if this session runs the Vulkan viewport, repeat
the same look-and-press and record whether the handles draw there. The reference
corpus does not document HUD behaviour under Vulkan, so **NOT RUN** is the
correct verdict when Vulkan is not in use.

## Radial menu

### Step 11 — radial menu builds, checks ON, and runs its three actions

In the Python Shell:

```python
import sys
sys.path.insert(0, r"%HOUDINI_RT_ROOT%\extensions\rt_toggle\houdini")
import rt_radial
menu = rt_radial.build()
print(menu.name(), [i.label() for i in menu.root().items().values()])
```

Then assign a key to the `RT` menu in **Edit > Radial Menus**, open it there,
and try each item.

**Pass when:** the menu shows **RT Toggle**, **RT Turn** and **RT Turn Cancel**
with their icons; Toggle carries a tick while the runtime is ON and loses it
when OFF; Turn submits a turn; Cancel reaches `CANCELLING`. A menu that builds
but never ticks is a FAIL on the check expression only — record that
separately, because the actions can still work.

**Why this needs the GUI:** hython 22.0.429 has no `hou.ui` attribute at all, so
the radial API cannot be exercised headlessly. That was confirmed in this build,
not assumed.

## Recording rules

- One verdict per step, plus the visible text or a screenshot name.
- A traceback, a frozen UI, a control reporting completion that
  `/turn/<id>` contradicts, or a parm that never updates is a **real finding**
  in 22.0.429.
- A refusal showing UNSAFE, or "sidecar is not running" while the runtime is
  OFF, is the design working — record PASS.
- When a step is skipped because the machine could not exercise it, record
  **NOT RUN** and the reason.

## After this list

- PASS on all eleven converts the "Not proven" section of
  `RELEASE_NOTES_v0.1.4.md` into evidence; carry the table into the next
  release notes.
- Then, and only then, repeat steps 3, 8, 9, 10 and 11 in 22.0.466 and 22.0.492.
  The shelf schema, parm templates and HUD rendering are the parts most likely
  to drift; the sidecar side is a separate 3.14 process and does not change per
  build.
