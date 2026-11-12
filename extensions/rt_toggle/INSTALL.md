# RT Toggle — Houdini 22.0.429 install

Packaging and install notes for the RT toggle extension. Written against SideFX
documentation and the extension's own files; nothing in this file was executed
in Houdini. All runtime checks live in `verification/INSTALL_CHECKS.md` and are
marked NOT RUN.

Build facts this install depends on:

- Houdini **22.0.429** runs **Python 3.13.10**.
- HoudiniRT is **Python 3.14**; `%HOUDINI_RT_ROOT%\Lib\site-packages`
  contains compiled `cp314` binaries (for example
  `charset_normalizer\md.cp314-win_amd64.pyd`, `coverage\tracer.cp314-win_amd64.pyd`,
  `OpenEXR.cp314-win_amd64.pyd`).
- The RT runtime can only be reached through the extension's compat guard
  (`rt_runtime\compat.py`). The compat guard is the only allowed gate.

Names fixed by the extension files (do not rename independently):

| Thing | Value | Defined in |
|---|---|---|
| Python Panel interface name | `RT_Toggle` | `houdini\rt_panel.pypanel`, `<interface name="RT_Toggle">` |
| Panel menu item id | `pythonpanel::RT_Toggle` | `houdini\PaneTabTypeMenu.xml` |
| Shelf set | `RT` | `houdini\rt_shelf.shelf` |
| Shelf tab | `RT` | `houdini\rt_shelf.shelf` |
| Shelf tool | `rt_toggle` | `houdini\rt_shelf.shelf` |
| Runtime package | `rt_runtime` | `rt_runtime\__init__.py` |
| Runtime config (not a Houdini package) | `rt_toggle.json` at the extension root | `rt_runtime\session.py`, `default_config_path()` |

There are two different files called `rt_toggle.json`:

- `%RT_TOGGLE_ROOT%\rt_toggle.json`
  is the runtime target config read by `rt_runtime.session.get_adapter()`. It is
  owned by the runtime component and is read in place; it is not installed.
- `%RT_TOGGLE_ROOT%\houdini\packages\rt_toggle.json`
  is the Houdini package file installed in Step 1. It appends the extension root
  named by `RT_TOGGLE_ROOT` to `PYTHONPATH`. Set both roots in `houdini.env`.

---

## Step 0 of 6 — Find the actual preference directory

Do this first. Every later path is relative to it.

1. Launch Houdini 22.0.429.
2. Open the Python Shell (Windows menu, or a Textport) and run:

   ```python
   import hou
   print(hou.applicationVersionString())
   print(hou.homeHoudiniDirectory())
   ```

3. Expect `22.0.429` and a path such as
   `%USERPROFILE%\Documents\houdini22.0`.

On this workstation `%USERPROFILE%\Documents\houdini20.5` and
`%USERPROFILE%\Documents\houdini21.0` exist, but **`houdini22.0` does not exist
yet**. Houdini creates it on first launch of 22.0. If `HOUDINI_USER_PREF_DIR` is
set to a custom location, `hou.homeHoudiniDirectory()` reports that location and
it wins over the Documents default.

Throughout this file, `%PREF%` means the value printed by
`hou.homeHoudiniDirectory()`.

---

## Step 1 of 6 — Copy the package file (runtime imports)

Source: `%RT_TOGGLE_ROOT%\houdini\packages\rt_toggle.json`

Copy it to:

```
%PREF%\packages\rt_toggle.json
```

For the default preference directory that is:

```
%USERPROFILE%\Documents\houdini22.0\packages\rt_toggle.json
```

What the file does: it appends the extension root named by `RT_TOGGLE_ROOT` to
`PYTHONPATH` at startup. That is the folder that contains `rt_runtime`, so
`import rt_runtime` works in Houdini's Python and the panel no longer shows
"rt_runtime not on sys.path". The same variable lets the shelf and panel resolve
their SVG icons in `houdini/icons`. It does not touch `HOUDINI_PATH`,
`HOUDINI_TOOLBAR_PATH`, or any HoudiniRT site-packages directory.

The package carries no machine path. Its `value` is `"$RT_TOGGLE_ROOT"`, so
Houdini must know both roots before it reads the package. Set them in
`%PREF%\houdini.env`:

```
HOUDINI_RT_ROOT = "<absolute path to the external HoudiniRT runtime root>"
RT_TOGGLE_ROOT = "<absolute path to the extension root>"
```

The runtime root is the folder that holds `python.exe` and `Lib\site-packages`:
the external Python 3.14 distribution. The extension root is the folder holding
`rt_toggle.json`, `rt_runtime` and `houdini`, now normally
`Houdini_RT/extensions/rt_toggle` in the source repository. They are independent;
do not point one at the other. Use forward slashes or escaped backslashes.

The JSON uses only documented package keys:

- `enable` — turns the package on.
- `show` — lists the package in the Package Browser window.
- `env` — an array of `{ "var", "value", "method" }` objects; `method: append`
  adds the value at the end of the existing `PYTHONPATH`.

Package files are read only at startup, so restart Houdini after copying this
file. The `name` key used by some packages on this machine is **not** in the
documented keyword list, so it is deliberately omitted here.

---

## Step 2 of 6 — Copy the Python Panel definition

Source:

```
%RT_TOGGLE_ROOT%\houdini\rt_panel.pypanel
```

Copy it to:

```
%PREF%\python_panels\rt_panel.pypanel
```

Default:

```
%USERPROFILE%\Documents\houdini22.0\python_panels\rt_panel.pypanel
```

Houdini scans `$HOUDINI_USER_PREF_DIR/python_panels` at startup and loads every
`.pypanel` in alphabetical filename order. If `$HOUDINI_PATH` is set, Houdini
instead scans the `python_panels` subdirectory of each `HOUDINI_PATH` entry.
`$HOUDINI_PYTHON_PANEL_PATH` overrides both. The filename does not matter; the
interface's internal `name` does, and it must stay `RT_Toggle`.

The `.pypanel` is self-contained: its widget script lives in a CDATA block, so
it does not need `rt_panel.py` beside it. The readable copy
`houdini\rt_panel.py` is for editing only; keep the two in sync if you change
the panel.

---

## Step 3 of 6 — Copy the shelf file and its module

The shelf XML holds a thin bootstrap that imports `rt_shelf.py`, whose readable
copy lives in `houdini\rt_shelf.py`. Copy both, together, into the toolbar
directory so the bootstrap finds its module next to the `.shelf` file.

Sources:

```
%RT_TOGGLE_ROOT%\houdini\rt_shelf.shelf
%RT_TOGGLE_ROOT%\houdini\rt_shelf.py
```

Copy both to:

```
%PREF%\toolbar\rt_shelf.shelf
%PREF%\toolbar\rt_shelf.py
```

Default:

```
%USERPROFILE%\Documents\houdini22.0\toolbar\rt_shelf.shelf
%USERPROFILE%\Documents\houdini22.0\toolbar\rt_shelf.py
```

Houdini loads every `.shelf` file found on `HOUDINI_TOOLBAR_PATH`. The default
value of that variable is `HOUDINI_PATH/toolbar`, and the preference directory is
on `HOUDINI_PATH`, so the copies above are enough and no environment change is
needed.

The bootstrap resolves `rt_shelf.py` in this order: `RT_TOGGLE_ROOT`, then
`HOUDINI_TOOLBAR_PATH` entries and their parents. A copy in `%PREF%\toolbar` is
found because that folder is the toolbar path entry, and no extension path is
hardcoded in the `.shelf`.

`rt_shelf.py` then resolves the runtime root: `RT_TOGGLE_ROOT`, then its own
parent folder if it contains `rt_runtime`. A copy in `%PREF%\toolbar` has no
`rt_runtime` beside it, and its parent is the preference directory, so a copy
install requires `RT_TOGGLE_ROOT` set to the extension root. Leaving the pair in
the extension's `houdini` folder and putting that folder on
`HOUDINI_TOOLBAR_PATH` needs no extra variable, because the parent folder does
contain `rt_runtime`.

Alternative (no copies): append the extension's `houdini` folder to
`HOUDINI_TOOLBAR_PATH` in `%PREF%\houdini.env`, for example

```
HOUDINI_TOOLBAR_PATH = "%RT_TOGGLE_ROOT%/houdini;&"
```

`houdini.env` is read at startup only. The package JSON intentionally does not
set this variable: it owns imports and the icon root only, and does not alter
Houdini's shelf search path.

---

## Step 4 of 6 — Install the pane tab type menu supplement

Source:

```
%RT_TOGGLE_ROOT%\houdini\PaneTabTypeMenu.xml
```

Copy it to:

```
%PREF%\PaneTabTypeMenu.xml
```

Default:

```
%USERPROFILE%\Documents\houdini22.0\PaneTabTypeMenu.xml
```

This file is a supplement. Do **not** copy the factory file
`C:\Program Files\Side Effects Software\Houdini 22.0.429\houdini\PaneTabTypeMenu.xml`.
Houdini loads the factory definitions first, then applies the same-named file
from `$HOUDINI_MENU_PATH` (the preference directory is on that path by default).
The supplement declares one item only:

```xml
<actionItem id="pythonpanel::RT_Toggle">
    <label>RT Toggle</label>
    <insertAfter>pythonpanel::log_viewer</insertAfter>
</actionItem>
```

`insertAfter` with an existing id places the item directly after that item in the
root pane menu. `pythonpanel::log_viewer` exists in the 22.0.429 factory file
(line 101), so the anchor is present in this build.

If `%PREF%\PaneTabTypeMenu.xml` already exists from another tool, do not overwrite
it. Merge the single `<actionItem>` block into its `<menu>` instead.

Note: interfaces named in `PaneTabTypeMenu.xml` are ignored by the Python Panel
Editor's own menu definition. That is expected, not an error.

---

## Step 5 of 6 — Load in-session (no restart), then add the pane tab

Use this when you want the panel now instead of after a restart. Run it in the
Houdini Python Shell (main application thread).

```python
import hou

pypanel_path = "%RT_TOGGLE_ROOT%/houdini/rt_panel.pypanel"
hou.pypanel.installFile(pypanel_path)

# Verify the interface is now installed.
print(hou.pypanel.interfaceByName("RT_Toggle"))

# Create a Python Panel pane tab bound to the RT Toggle interface.
pane = hou.ui.curDesktop().panes()[0]
tab = pane.createTab(hou.paneTabType.PythonPanel, "RT_Toggle")
```

`hou.pypanel.installFile(file_path)` installs every interface defined in the
`.pypanel` into the current session (documented in `hou.pypanel`).
`hou.Pane.createTab(type, python_panel_interface)` creates the pane tab; passing
the interface name also hides the Python Panel toolbar (documented in
`hou.Pane`).

If a Python Panel tab already exists, bind it instead:

```python
import hou
tab = hou.ui.paneTabOfType(hou.paneTabType.PythonPanel)
tab.setActiveInterface(hou.pypanel.interfaceByName("RT_Toggle"))
```

`setActiveInterface` raises `hou.OperationFailed` if the interface is not
installed, so run `installFile` first.

The shelf and the pane menu item are startup-loaded. Neither appears from an
in-session copy; restart Houdini for those. The `.pypanel` can be loaded either
way.

---

## Step 6 of 6 — Hard warning: never add HoudiniRT site-packages

Do **not** put this directory on Houdini's `PYTHONPATH`, in the package JSON, in
`houdini.env`, or in a startup script:

```
%HOUDINI_RT_ROOT%\Lib\site-packages      ← NEVER
```

Why:

- Houdini 22.0.429 embeds Python 3.13.10. HoudiniRT's site-packages is built for
  Python 3.14.
- It contains compiled `cp314` extension modules such as
  `charset_normalizer\md.cp314-win_amd64.pyd`, `coverage\tracer.cp314-win_amd64.pyd`,
  `aiohttp\_http_parser.cp314-win_amd64.pyd`, and `OpenEXR.cp314-win_amd64.pyd`.
  Python 3.13 cannot load a `cp314` binary module; the import fails, and depending
  on the module the failure can take down the process rather than raise cleanly.
- The same directory also contains hundreds of packages (`numpy`, `aiohttp`,
  `OpenEXR`) that would shadow or conflict with Houdini's own bundled modules.

The extension root added by the package is a different directory and contains
only the extension's own Python. That is the only HoudiniRT-adjacent path allowed
on `PYTHONPATH`.

The compat guard inside the extension is the only allowed gate to the Python 3.14
runtime. It works from filenames only and never imports the modules it inspects.
If a code path needs a HoudiniRT module that only exists in `Lib\site-packages`,
the correct result is a refusal from the guard (state OFF, `ok=False`, reasons
naming the ABI mismatch), not a `PYTHONPATH` addition.

---

## Uninstall

Close Houdini first. Then delete the installed files (all under `%PREF%`), and
the optional shelf module copy:

1. `%PREF%\packages\rt_toggle.json`
2. `%PREF%\python_panels\rt_panel.pypanel`
3. `%PREF%\toolbar\rt_shelf.shelf`
4. `%PREF%\toolbar\rt_shelf.py`
5. `%PREF%\PaneTabTypeMenu.xml`

If `%PREF%\PaneTabTypeMenu.xml` belonged to another tool before this install,
delete only the `<actionItem id="pythonpanel::RT_Toggle"> … </actionItem>` block
and keep the rest of the file.

Do not use the Python Panel Editor's Delete Interfaces dialog for this. That
dialog deletes interface definitions from disk and is documented as irreversible;
deleting the file directly gives the same result with a clearer trail.

Restart Houdini, then run the uninstall check in
`verification/INSTALL_CHECKS.md`. The extension root stays where it is on disk;
removing the package file is enough to remove it from `PYTHONPATH`.

If an earlier toggle left the runtime ON, the adapter wrote
`%RT_TOGGLE_ROOT%\.rt_toggle_state.json`
(default state file). The uninstall does not need to delete it; the next session
will not load the runtime unless a toggle is pressed again. Delete it only if
you want a completely clean slate.

---

## File map

| Installed file | Source in the extension |
|---|---|
| `%PREF%\packages\rt_toggle.json` | `houdini\packages\rt_toggle.json` |
| `%PREF%\python_panels\rt_panel.pypanel` | `houdini\rt_panel.pypanel` |
| `%PREF%\toolbar\rt_shelf.shelf` | `houdini\rt_shelf.shelf` |
| `%PREF%\toolbar\rt_shelf.py` | `houdini\rt_shelf.py` |
| `%PREF%\PaneTabTypeMenu.xml` | `houdini\PaneTabTypeMenu.xml` |

Read in place, not installed: `rt_runtime\` and the runtime config
`rt_toggle.json` at the extension root.

Source root:
`%RT_TOGGLE_ROOT%`

Nothing in this install writes to `D:\Houdini_AI_Reference` or to
`%HOUDINI_RT_ROOT%\Lib\site-packages`.

---

## Documented vs unverified

Documented in the 22.0.429 installed help:

- Package JSON keywords and the `var` / `value` / `method` env syntax
  (`help:22.0.429/ref.zip/plugins.txt`).
- `.pypanel` search locations and startup loading, the
  `pythonpanel::<interface>` pane menu id, and the fact that a `.pypanel` is
  self-contained
  (`help:22.0.429/ref.zip/windows/pythonpaneleditor.txt`).
- Pane menu supplements and `insertBefore` / `insertAfter`
  (`help:22.0.429/basics.zip/config_menus.txt`, and the factory
  `PaneTabTypeMenu.xml` header).
- `.shelf` loading from `HOUDINI_TOOLBAR_PATH`, default `HOUDINI_PATH/toolbar`
  (`help:22.0.429/shelf.zip/config_file.txt`).
- `hou.pypanel.installFile`, `hou.pypanel.interfaceByName`,
  `hou.Pane.createTab`, `hou.PythonPanel.setActiveInterface`,
  `hou.homeHoudiniDirectory` (`help:22.0.429/hom.zip/...`).

Verified from the extension files (not executed in Houdini):

- Interface name `RT_Toggle` and the `.pypanel` being self-contained.
- Shelf set / shelf / tool names (`RT`, `RT`, `rt_toggle`) and the bootstrap's
  file-resolution order.
- The runtime's config default path and state filename.

Unverified here (no Houdini session was launched):

- That the supplement renders the menu item in 22.0.429 in the intended
  position. The anchor id is present in the factory file; the merge behavior is
  documented but was not executed.
- That the panel widget renders correctly under Houdini's PySide6.
- The exact refusal text of the compat guard on 22.0.429.
- That the runtime config `rt_toggle.json` exists and names a valid target. If it
  is missing, `get_adapter()` raises `LookupError: no runtime targets
  configured` and checks 2 and 3 cannot run.

The riskiest step is **Step 4**: writing a same-named `PaneTabTypeMenu.xml` into
the preference directory. If another tool already installed one there, copying
over it silently loses that tool's menu edits. Check the file before copying and
merge instead of overwrite.
