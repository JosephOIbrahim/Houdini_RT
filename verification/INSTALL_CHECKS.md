# RT Toggle — install checks for Houdini 22.0.429

Six checks a human can run in Houdini 22.0.429 after following `INSTALL.md`.

**No check in this file has been run.** No Houdini session and no hython session
was launched by the packaging agent. Every result below is NOT RUN.

Run each check in the Houdini Python Shell (main application thread) unless the
step says otherwise. Names used throughout:

- interface internal name: `RT_Toggle`
- shelf set: `RT`
- shelf tab: `RT`
- shelf tool: `rt_toggle`
- runtime package: `rt_runtime`
- runtime config (read in place): `%HOUDINI_RT_ROOT%\extensions\rt_toggle\rt_toggle.json`

If `rt_runtime` cannot be imported, the package file from Step 1 of `INSTALL.md`
is not installed or Houdini was not restarted. Confirm with:

```python
import rt_runtime
print(rt_runtime.__version__, rt_runtime.__file__)
```

---

## Check 1 of 6 — The Python Panel opens

Goal: the `.pypanel` is installed and its interface can be displayed.

Steps:

1. Launch Houdini 22.0.429.
2. In the Python Shell, run:

   ```python
   import hou
   print(hou.applicationVersionString())
   print(sorted(hou.pypanel.interfaces().keys()))
   print(hou.pypanel.interfaceByName("RT_Toggle"))
   ```

3. Then open the pane tab: click the `+` on a pane tab strip, choose
   **New Pane Tab Type**, and select **RT Toggle**.

Pass:

- `hou.applicationVersionString()` prints `22.0.429`.
- `RT_Toggle` is in the interface list.
- `interfaceByName("RT_Toggle")` returns a `hou.PythonPanelInterface`, not
  `None`.
- The new pane tab opens and draws the RT Toggle interface with no Python
  traceback dialog. With the runtime off, the panel shows its OFF state and the
  adapter status, not "rt_runtime not on sys.path".

Fail: `None` for the interface means the `.pypanel` was not found in
`%PREF%\python_panels` or its internal name differs. A traceback on open means
the panel script failed; read `tab.activeInterfaceScriptErrors()`.

Result: **NOT RUN**

---

## Check 2 of 6 — The RT toggle is refused on this build

Goal: the compat guard refuses to put the Python 3.14 runtime on Houdini's
Python 3.13 `sys.path`, and refuses cleanly.

Steps:

1. Confirm the interpreter:

   ```python
   import sys
   print(sys.version)
   ```

2. Probe the default runtime target (read-only; changes nothing):

   ```python
   from rt_runtime import get_adapter
   a = get_adapter()
   print(a.target.name)
   print(a.probe())
   ```

3. Attempt activation, exactly as the panel toggle and shelf tool do:

   ```python
   r = a.activate()
   print(r.ok, r.state, r.changed)
   print(r.reasons)
   print(r.error)
   ```

4. Also press the toggle in the panel and the shelf button.

Pass:

- `sys.version` reports Python 3.13.x (expected 3.13.10).
- `a.probe()["compatibility"]["safe"]` is `False`, and `blockers` names compiled
  `cp314` extensions or the interpreter ABI mismatch.
- `r.ok` is `False`, `r.state` is OFF (or ERROR if the target path is missing),
  `r.changed` is `False`.
- `r.reasons` names the compatibility refusal.
- The panel and shelf report a refusal line; Houdini stays alive and responsive,
  no process crash, no hard error dialog.
- After the refused attempt, no `cp314` module was imported. Probe:

  ```python
  import sys
  bad = []
  for name, mod in list(sys.modules.items()):
      f = getattr(mod, "__file__", None) or ""
      if "HoudiniRT" in f and "site-packages" in f:
          bad.append(name)
  print(bad)   # expect []
  ```

Fail: an `ImportError` naming a `*.cp314-win_amd64.pyd` file, or a Houdini crash,
means something put `%HOUDINI_RT_ROOT%\Lib\site-packages` on
`PYTHONPATH`. Remove it and re-read Step 6 of `INSTALL.md`. A `LookupError: no
runtime targets configured` means the runtime config is missing; fix the config,
not the guard.

Result: **NOT RUN**

---

## Check 3 of 6 — A pure-Python mismatch is refused by default, allowed only on request

Goal: pin both sides of the wired policy. A pure-Python target is **refused**
across an interpreter mismatch when `allow_pure_python_only` is false (the
default), and **allowed** when the target's `rt_toggle.json` entry sets it true.

Steps:

1. Create a scratch folder containing only pure-Python files, for example
   `F:\rt_pure_target\site-packages\demo_mod.py`.
2. Add a target entry to `rt_toggle.json` under `"targets"`:

   ```json
   {
       "name": "pure-demo",
       "home": "F:/rt_pure_target",
       "python_version": "3.14",
       "site_packages": "F:/rt_pure_target/site-packages",
       "allow_pure_python_only": false
   }
   ```

   The accepted keys are `name`, `home`, `python_version`, `site_packages`,
   `entry_point`, `env`, `allow_pure_python_only` (boolean, default false).
3. In Houdini (3.13.10), run the strict pass:

   ```python
   from rt_runtime import get_adapter

   a = get_adapter(target_name="pure-demo")
   print(a.probe()["compatibility"]["safe"])          # expected: False
   r = a.activate()
   print(r.ok, r.state)                                # expected: False OFF
   ```
4. Set `"allow_pure_python_only": true` in that entry, then in a fresh adapter:

   ```python
   a2 = get_adapter(target_name="pure-demo")
   print(a2.probe()["compatibility"]["safe"])          # expected: True
   r = a2.activate()
   print(r.ok, r.state)                                # expected: True ON
   a2.deactivate()
   ```

Pass:

- With false: the probe is unsafe and `activate()` refuses, because the declared
  3.14 target differs from the 3.13 host.
- With true: the probe is safe with reasons saying the tree has no compiled
  extensions, `activate()` returns ON, and `deactivate()` restores `sys.path`.
- Houdini remains stable throughout.

Fail: a safe probe with the default false means the strict default was lost. A
refusal with true means the config flag is not reaching the compatibility check.
Check that the scratch folder really contains no `.pyd` or `.so` files.

Result: **NOT RUN**

---

## Check 4 of 6 — The shelf appears

Goal: `rt_shelf.shelf` and its bootstrap module `rt_shelf.py` load from the
preference directory's `toolbar` folder.

Steps:

1. Install the shelf per Step 3 of `INSTALL.md`, then restart Houdini.
2. In the Python Shell, run:

   ```python
   import hou
   print(sorted(hou.shelves.shelves().keys()))
   print(sorted(hou.shelves.tools().keys()))
   print(hou.shelves.tool("rt_toggle"))
   ```

3. Look at the shelf bar in the main window and click the RT Toggle button.

Pass:

- The shelf tab `RT` appears in the shelf bar.
- `hou.shelves.shelves()` contains `RT`.
- `hou.shelves.tools()` contains `rt_toggle`, and
  `hou.shelves.tool("rt_toggle")` returns a `hou.Tool`.
- Clicking the button prints one `HoudiniRT:` status line and sets a status-bar
  message. It does not raise a traceback. With the 3.14 runtime targeted, the
  line reports the compatibility refusal from Check 2.

Fail: missing keys mean the file is not on `HOUDINI_TOOLBAR_PATH`. Confirm the
copy at `%PREF%\toolbar\rt_shelf.shelf` and `rt_shelf.py`. A status line reading
"rt_shelf.py not found" means the bootstrap could not resolve its module; keep
both files in the same folder or set `RT_TOGGLE_ROOT`.

Result: **NOT RUN**

---

## Check 5 of 6 — The pane menu item appears

Goal: the `PaneTabTypeMenu.xml` supplement merges into the factory pane menu and
shows exactly one RT Toggle entry.

Steps:

1. Install the supplement per Step 4 of `INSTALL.md`, then restart Houdini.
2. In the Python Shell, run:

   ```python
   import hou, os
   pref = hou.homeHoudiniDirectory()
   print(pref)
   print(os.path.exists(os.path.join(pref, "PaneTabTypeMenu.xml")))
   print(hou.pypanel.interfaceByName("RT_Toggle"))
   ```

3. Click the `+` on a pane tab strip and open **New Pane Tab Type**. Also
   right-click an existing pane tab to see the radio-button version of the menu.

Pass:

- The supplement file exists at `%PREF%\PaneTabTypeMenu.xml`.
- The interface is installed (not `None`).
- **RT Toggle** appears in the pane tab type menu, directly after the
  **Log Viewer** Python Panel entry (`insertAfter` anchor
  `pythonpanel::log_viewer`).
- There is exactly one RT Toggle entry in that menu.
- The Python Panel gear menu does not list a second RT Toggle entry. Interfaces
  named in `PaneTabTypeMenu.xml` are documented as ignored by the Python Panel
  menu definition.

Fail: no entry means the supplement was not found on `HOUDINI_MENU_PATH`, or the
XML did not parse. A duplicate entry means the interface was also added through
the Python Panel Editor's menu definition; remove it there.

Result: **NOT RUN**

---

## Check 6 of 6 — Uninstall restores the previous state

Goal: removing the installed files removes every trace of the extension without
touching anything else.

Steps:

1. Close Houdini.
2. Delete, under `%PREF%`:
   - `packages\rt_toggle.json`
   - `python_panels\rt_panel.pypanel`
   - `toolbar\rt_shelf.shelf`
   - `toolbar\rt_shelf.py`
   - `PaneTabTypeMenu.xml` (or only the `pythonpanel::RT_Toggle` block if the
     file belonged to another tool)
3. Restart Houdini.
4. In the Python Shell, run:

   ```python
   import hou, os
   print(hou.pypanel.interfaceByName("RT_Toggle"))           # expect None
   print(hou.shelves.tool("rt_toggle"))                      # expect None
   print([p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep)
          if "rt_toggle" in p])                              # expect []
   print(os.path.exists(os.path.join(hou.homeHoudiniDirectory(),
                                     "PaneTabTypeMenu.xml"))) # expect False
   ```

5. Open **New Pane Tab Type** and check the shelf bar.

Pass:

- `interfaceByName("RT_Toggle")` returns `None`.
- `hou.shelves.tool("rt_toggle")` returns `None`, and the `RT` shelf tab is gone.
- No entry containing `rt_toggle` remains in `PYTHONPATH`.
- RT Toggle is absent from the pane tab type menu.
- Any pre-existing `PaneTabTypeMenu.xml` content from other tools is still
  present.

Fail: a remaining menu item with the supplement deleted means Houdini is reading
a different `PaneTabTypeMenu.xml` from another directory on `HOUDINI_MENU_PATH`.
A remaining `PYTHONPATH` entry means the package JSON is still in a scanned
`packages` directory (`%PREF%\packages`, `$HSITE\houdini22.0\packages`,
`$HOUDINI_PACKAGE_DIR`, or `$HFS\packages`).

Result: **NOT RUN**
