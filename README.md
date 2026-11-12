# RT Toggle — HoudiniRT runtime on/off for Houdini 22.0.429

**What it is.** A small Houdini extension that turns the HoudiniRT Python runtime
on or off inside a running Houdini session. "On" means the runtime's
`site-packages` directory is appended to the current process's `sys.path` and the
target's environment variables are applied; "off" removes exactly the entries the
toggle added and restores the environment to its prior values.

The switch is honest about what it cannot do. Houdini 22.0.429 embeds Python
3.13.10 while HoudiniRT is a Python 3.14 distribution, so the full HoudiniRT
`site-packages` (104 `cp314` compiled modules) is refused by an ABI guard rather
than loaded. A refusal is a normal, tested outcome, not a crash.

**Status: scaffold.** The pure-Python core is covered by 74 local tests. Nothing
in this tree has been executed inside Houdini yet — see
[Proven vs untested](#proven-vs-untested).

---

## Surfaces

There are three supported ways to reach the toggle, and one surface that is
explicitly not available.

| Surface | Role | File | Shows live state |
|---|---|---|---|
| Python Panel | **Primary** | `houdini/rt_panel.pypanel` | Yes — full probe: target, Python version, ABI verdict, blockers, last error |
| Shelf tool | **Secondary** | `houdini/rt_shelf.shelf` (+ `rt_shelf.py`) | No — one status line and a status-bar message per click |
| Node | Optional, scene-scoped | `houdini/rt_node.py` | Last press only, in a string parm |
| Viewport camera dropdown | **Not an extension point** | — | — |

The camera dropdown in the top-right corner of a viewport is a closed, built-in
menu in 22.0.429. `docs/CLAIMS_VS_REFERENCE.md` and `DESIGN.md` §2.1 record the
evidence: no documented insertion API exists, only descriptions of the menu and
triggers for entries already in it. Do not build against it.

All three supported surfaces call the same process-wide adapter
(`rt_runtime.session.get_adapter()`), so they share one lock, one state file, and
one set of path bookkeeping. They cannot disagree about what was added to
`sys.path`.

---

## Quick start (Houdini 22.0.429)

Five files, four destinations. Full step-by-step detail, including the exact
`hou` calls and uninstall steps, is in **[INSTALL.md](INSTALL.md)**.

`%PREF%` below means the folder printed by `hou.homeHoudiniDirectory()`
(typically `%USERPROFILE%\Documents\houdini22.0`). `%HOUDINI_RT_ROOT%` is the
documentation spelling of the `HOUDINI_RT_ROOT` environment variable: the
HoudiniRT distribution root — the folder that contains `Lib\`, `Scripts\`, and
`extensions\`. `%USERPROFILE%` is your Windows profile folder. Substitute real
paths when you type them into Houdini.

**Step 1 of 5 — Make the extension importable.** Set `HOUDINI_RT_ROOT` to the
HoudiniRT distribution root in `%PREF%\houdini.env`, then copy
`houdini/packages/rt_toggle.json` to `%PREF%\packages\rt_toggle.json`. The
package appends `$HOUDINI_RT_ROOT/extensions/rt_toggle` to `PYTHONPATH` at
startup; it does not touch HoudiniRT `site-packages`. Package files are read at
startup only, so restart afterwards.

**Step 2 of 5 — Install the panel.** Copy `houdini/rt_panel.pypanel` to
`%PREF%\python_panels\rt_panel.pypanel`. The internal interface name `RT_Toggle`
is fixed and must not be renamed.

**Step 3 of 5 — Install the shelf.** Copy `houdini/rt_shelf.shelf` and
`houdini/rt_shelf.py` together into `%PREF%\toolbar\`. The `.shelf` bootstrap
looks for `rt_shelf.py` in the same folder, so they must travel as a pair, and
because `rt_runtime` is not in the preference folder, set `RT_TOGGLE_ROOT` to the
extension root. Without the copies, add
`%HOUDINI_RT_ROOT%\extensions\rt_toggle\houdini` to
`HOUDINI_TOOLBAR_PATH` in `%PREF%\houdini.env` instead; that needs no extra
variable.

**Step 4 of 5 — Add the pane menu entry.** Copy `houdini/PaneTabTypeMenu.xml` to
`%PREF%\PaneTabTypeMenu.xml`. This is a supplement, not a replacement for the
factory file; if a file with that name already exists from another tool, merge
the single `<actionItem>` block instead of overwriting it.

**Step 5 of 5 — Open it.** Restart Houdini, open the pane tab type menu, and
choose **RT Toggle**. The panel draws the current state and a toggle button. The
shelf tool **RT Toggle** does the same switch in one click.

You can skip the restart for the panel alone by running
`hou.pypanel.installFile(...)` in the Houdini Python Shell; the shelf and the
pane menu entry are startup-loaded. See `INSTALL.md` Step 5.

**Do not** put `%HOUDINI_RT_ROOT%\Lib\site-packages` on `PYTHONPATH`, in a
package JSON, in `houdini.env`, or in a startup script. The ABI guard exists
precisely to keep that directory out of Houdini's interpreter.

---

## Runtime configuration

`rt_toggle.json` at the extension root names the runtime target. Paths in it are
relative to that file's folder:

```json
{
  "version": 1,
  "default": "houdini_rt",
  "targets": {
    "houdini_rt": {
      "home": "../..",
      "python_version": "3.14",
      "site_packages": "../../Lib/site-packages",
      "entry_point": null,
      "env": {},
      "allow_pure_python_only": false
    }
  }
}
```

`../..` resolves to the HoudiniRT distribution root and `../../Lib/site-packages`
to its package tree, so the file works wherever the checkout lives. Accepted
target keys are `name`, `home`, `python_version`, `site_packages`, `entry_point`,
`env`, and `allow_pure_python_only`. Relative values resolve against the config
file's folder; absolute values still work.

---

## ABI policy

The compatibility guard (`rt_runtime/compat.py`) compares the host interpreter's
ABI against the target's declared `python_version` and scans the target for
ABI-specific binaries. It reads filenames only; it never imports what it
inspects.

**Strict by default.** A target whose declared Python version differs from the
host is refused, even when the target contains no compiled extensions. Under
Houdini's `cp313` host, the default `houdini_rt` target — Python 3.14 with 104
`cp314` `.pyd` files — is refused with an ABI-mismatch blocker. That refusal is
the designed, correct result.

**Opt-in per target.** Setting `"allow_pure_python_only": true` on a target
permits activation when the host version differs *and* the scan finds no
compiled binaries, and the report carries a warning that 3.14-only syntax or
standard-library APIs may still fail at import or call time. The flag is
per-target; one config can carry a strict production target and a permissive
scratch target.

Activation never imports `entry_point`, never changes the interpreter, and never
unloads modules on deactivate. What "on" means in full is in `DESIGN.md` §5.

---

## Tests

From the extension root:

```powershell
py -3.12 -m pytest tests -q
```

Expected: **74 passed**. The suite runs on a normal CPython 3.12 and needs no
Houdini, no GUI, and no hython. `tests/fake_hou.py` supplies a pure-Python stand-in
for `hou` where a UI surface is exercised.

Do not run the suite under the 3.14 interpreter on this workstation; use
3.12. Details and per-claim commands are in `verification/RUN.md`.

---

## Proven vs untested

**Proven locally (CPython 3.12.10, outside Houdini):**

- 74 pytest tests pass: adapter state machine, idempotency, exact `sys.path`
  restoration, env apply/restore, state-file schema and persistence, ABI guard
  decisions including the cp314 refusal and the opt-in allowance, and the XML /
  script contracts of the `.pypanel` and `.shelf`.
- `load_targets()` resolves the relative config to the expected home and
  `site-packages` paths.
- The guard refuses the full 3.14 `site-packages` when told the host is 3.13.

**Untested (requires a Houdini run, which this scaffold has not done):**

- That the `.pypanel` installs and renders under Houdini's PySide6.
- That `hou.pypanel.installFile` accepts the file as written.
- That the pane menu supplement merges into the factory menu at the intended
  position.
- That the shelf loads from `HOUDINI_TOOLBAR_PATH` and its bootstrap resolves
  `rt_shelf.py`.
- That `rt_node.build()` creates the node and that the parm callback fires, and
  whether a programmatic `Parm.set()` re-enters that callback.
- The guard's exact refusal text inside Houdini's own 3.13 interpreter, and the
  precise symptom of importing a `cp314` `.pyd` under 3.13.
- Whether interface names are matched case-sensitively.

`verification/MATRIX.md` keeps every Houdini-side row at `not_run` or `proposed`.
`verification/INSTALL_CHECKS.md` holds the six manual checks that would promote
them.

---

## Layout

```
rt_toggle/
├─ README.md                     this file
├─ INSTALL.md                    step-by-step Houdini install and uninstall
├─ DESIGN.md                     design record, state machine, ABI policy
├─ rt_toggle.json                runtime target config (relative paths)
├─ rt_runtime/                   pure-Python core; never imports hou
│  ├─ adapter.py                 RuntimeTarget, RuntimeAdapter, state file
│  ├─ compat.py                  ABI / compatibility guard
│  └─ session.py                 get_adapter() singleton
├─ houdini/
│  ├─ rt_panel.pypanel           Python Panel (self-contained)
│  ├─ rt_panel.py                readable copy of the panel script
│  ├─ rt_shelf.shelf             shelf set + tool bootstrap
│  ├─ rt_shelf.py                shelf helper module
│  ├─ rt_node.py                 optional node builder
│  ├─ RT_TOGGLE_NODE.md          node recipe and limitations
│  ├─ PaneTabTypeMenu.xml        pane tab type menu supplement
│  └─ packages/rt_toggle.json    startup PYTHONPATH package
├─ docs/CLAIMS_VS_REFERENCE.md   what the local docs corpus does and does not say
├─ tests/                        pytest suite (74 tests)
└─ verification/                 install checks, claim matrix, run book
```

The state file `.rt_toggle_state.json` is written at the extension root beside
`rt_toggle.json`. It is generated at runtime and is not tracked.

---

## License

License: TBD. No license file is included; licensing is the owner's decision.

---

*RT Toggle v0.1.1 · Myth and Logic*
