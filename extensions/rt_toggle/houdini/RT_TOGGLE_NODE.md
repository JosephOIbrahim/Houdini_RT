# RT_TOGGLE — the node-level on/off option

**What this is.** `rt_node.py` builds one small null node named `RT_TOGGLE`
with two spare parms and a Python parm callback. It is Houdini-scene furniture,
not a panel: the switch lives in the network, saves with the `.hip`, and can be
selected, referenced, expression-driven, or wrapped in an HDA.

The node type is the built-in `null`. Under `/obj` it resolves to Object/null;
passed a geometry network as `parent`, it resolves to Sop/null. Both are exact
type names in the 22.0.429 installed help
(`help:22.0.429/nodes.zip/obj/null.txt`, `.../sop/null.txt`).

---

## Parms

| Internal name | Label | Type | Behavior |
|---|---|---|---|
| `enable` | RT Runtime | Toggle | The request switch. Changing it runs the callback. |
| `rt_status` | RT Status | String, locked | Result of the last change. Starts with `OFF`, `ON`, `REFUSED` or `ERROR`; anything after the ` \| ` separator is diagnostic detail (`state=… changed=…`, refusal reasons, or the exception). |

The callback is stored on `enable` as a one-line Python script. It inserts the
extension paths into `sys.path`, imports `rt_node`, and calls
`rt_node.toggle_callback(kwargs)`.

---

## How to run it

Step 1 of 3 — make the extension importable in the Houdini Python Shell.

```python
import sys
sys.path.insert(0, r"<extension>\houdini")   # for rt_node
sys.path.insert(0, r"<extension>")           # for rt_runtime
import rt_node
```

Step 2 of 3 — build the node.

```python
rt_node.build()          # creates /obj/RT_TOGGLE
```

Step 3 of 3 — select `/obj/RT_TOGGLE` and click the **RT Runtime** checkbox.
The status parm reports the outcome immediately.

Until the `rt_runtime` package exists, pressing the toggle writes
`ERROR | rt_runtime not importable (…)` and puts the checkbox back — the
graceful refusal path, not a traceback in the shell.

---

## What a node toggle can do that a Python Panel cannot

- **Scene-scoped state.** The `enable` value, the `rt_status` text and the
  callback script itself are saved inside the `.hip`. Two shots can carry two
  independent switches.
- **Network citizenship.** The node can be selected, referenced by expressions,
  driven by a CHOP, included in an HDA, or wired into a larger tool.
- **No panel lifecycle.** There is nothing to open or dock; it is a node in the
  scene, so it is present exactly when the scene is.

## What a node toggle cannot do

1. **It is scene-scoped, not session-scoped.** The switch does not exist until
   a node is built in a scene, and it does not follow the user into the next
   `.hip` unless that file also contains the node. The Python Panel is a
   session-wide control that can be opened with no scene at all.
2. **It needs the extension on `sys.path`.** The callback is a saved string
   containing absolute paths from build time. On a machine without those paths
   (or after the extension moves), the press fails at import time. Re-run
   `build()` after moving the extension to re-bake the paths.
3. **A parm callback cannot run before the node exists.** The callback is
   attached to `enable` by `build()`; it fires only on a value change after the
   node and parm exist. It is not a startup hook: opening the `.hip` does not
   run it, and there is no "restore the runtime on load" behavior.
4. **It cannot show a live ABI verdict without extra parms.** A parm callback
   runs only when a value changes. There is no polling, timer, or idle hook in
   this node, so `rt_status` shows the verdict of the *last press*, not current
   readiness. A live ABI verdict would need extra machinery: a Refresh button
   parm plus verdict/detail string parms, or a session-level event callback —
   none of which this two-parm node includes.
5. **The checkbox is a request, not a mirror.** If the runtime is turned on or
   off elsewhere, the checkbox does not update. It also treats every change as
   "call `toggle()`": the contract offers no explicit set-on/set-off call, so
   an out-of-band state change can make the next press move the runtime in the
   direction opposite to the label.
6. **Runtime state is not scene state.** The `.hip` saves what was asked
   (the parm value and last reported status). Whatever `rt_runtime` did is not
   restored by loading the file.

---

## The camera dropdown question

**The viewport camera dropdown is not extensible.** There is no supported
extension point for adding an entry to the camera menu, so this node is the
"node" option the request asked for.

---

## Turning the script into an HDA

**Recipe only — not executed in this task.**

1. Run `rt_node.build()` in the scene you want to wrap.
2. Select `/obj/RT_TOGGLE` and choose **Create Digital Asset**, or call the
   documented HOM method (`docs:hom/hou/OpNode.md#createDigitalAsset`):

   ```python
   node = hou.node("/obj/RT_TOGGLE")
   node.createDigitalAsset(
       name="rt_toggle",
       hda_file_name=r"$HOME/houdini22.0/hda/rt_toggle.hda",
       description="RT Toggle",
   )
   ```

3. The two spare parms become the HDA interface parms. `enable` keeps its
   callback string; `rt_status` is the display parm.
4. For a portable asset, move `toggle_callback` (and its helpers) into the
   HDA's **PythonModule** section and point the `enable` callback at the
   documented asset-module pattern:

   ```python
   hou.pwd().hdaModule().toggle_callback(kwargs)
   ```

   `hou.pwd()` returns the cooking node in a callback; `OpNode.hm()` /
   `hdaModule()` are the documented way to reach the asset's Python module
   (`docs:hom/locations.md#parameter_callback_scripts`).
5. Install the resulting file anywhere with
   `hou.hda.installFile(path)` (`docs:hom/hou/hda.md#installFile`).

**HDA caveat.** On a locked asset instance, `Parm.lock(False)` raises
`hou.PermissionError`. `_set_status` catches that and attempts the plain
`Parm.set()` instead. Whether a locked HDA interface parm accepts that
programmatic write is not stated in the local docs — it is part of the live
check below.

---

## Files

- `extensions/rt_toggle/houdini/rt_node.py` — `build()`, `toggle_callback()`,
  `callback_script()`, and the status helpers.
- `extensions/rt_toggle/houdini/RT_TOGGLE_NODE.md` — this page.

Expected layout for imports:

```
extensions/rt_toggle/          <- sys.path entry for rt_runtime
    houdini/                   <- sys.path entry for rt_node
        rt_node.py
        RT_TOGGLE_NODE.md
    rt_runtime/
        session.py             (get_adapter(), built elsewhere)
```

---

## Verification status

- **Documented:** every HOM name used — `hou.ToggleParmTemplate`,
  `hou.StringParmTemplate`, `hou.ParmTemplateGroup`, `OpNode.parmTemplateGroup`,
  `OpNode.setParmTemplateGroup`, `Parm.lock` / `Parm.isLocked`,
  `script_callback` / `script_callback_language`, the callback `kwargs` keys
  (`docs:hom/locations.md#parameter_callback_scripts`), and the `null` node
  type names in obj and sop contexts.
- **Recipe only:** `build()`, the HDA conversion, and the `.hip` round-trip
  were not executed (no Houdini or hython run in this task).
- **Needs a live Houdini check:** whether the revert in `_refuse` (a
  programmatic `Parm.set()` on `enable`) re-enters the parm callback
  synchronously. `_TOGGLE_IN_PROGRESS` blocks the synchronous case; a deferred
  callback could fire a second `adapter.toggle()`. Run one refused toggle and
  count the `toggle()` calls.
