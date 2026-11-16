# Integrated viewer milestone — v0.2.0

Target: Houdini 22.0.429, with its matching HoudiniRT plugin build.

## Recorded result — 2026-11-16

The isolated desktop smoke passed on Houdini 22.0.429. It exercised the actual
Qt panel with a fresh build of the unchanged renderer and a scratch USD scene:

- Started from Karma CPU with its native pause flag true.
- Enabled HoudiniRT, then confirmed native pause and resume through the panel.
- Destroyed/recreated the panel while paused; ownership and Resume survived.
- Opened a second Solaris viewer. Original-target pause/resume and Off left
  the second viewer's Karma CPU selection and unpaused state unchanged.
- Off restored the original Karma CPU selection and original paused state.
- The isolated process exited cleanly through native quit.

Local evidence is under the repository's ignored build directory:
`build/m1-viewer/gui-smoke-20261116-01/result-final.json`, its launch/log files,
and `build/m1-viewer/build_provenance.json`. Earlier failed diagnostic runs
remain alongside it. These are local receipts, not bundled release artifacts
or substitutes for the sealed renderer exams.

The full extension suite passed with the external Python 3.14 runtime:
**377 passed, 1 skipped**. Python 3.12 also passed its applicable tests:
**360 passed, 18 skipped** (including runtime-specific tests).

## Behavior under test

The artist selects a Solaris Scene Viewer, enables HoudiniRT, observes native
renderer/pause state, pauses/resumes, then switches Off to restore the prior
renderer and pause flag. All surfaces share restoration ownership. A manual
renderer switch relinquishes that ownership. No assistant process is required.

This milestone does not measure first-frame latency, pixel correctness,
convergence or GPU resource release. The renderer implementation is unchanged.
The original sealed renderer exams remain separate acceptance evidence.

## Automated tests

Run from the extension root with Python 3.12 or newer and pytest:

```powershell
python -B -m pytest tests -q -p no:cacheprovider
```

Set `RT_SIDECAR_LIVE_MODEL=0` for the full suite. It needs local loopback
networking for its existing transport tests; those tests never require a
live model endpoint. Rendering tests use an injected HOM stand-in and assert
observable behavior, including failures and multi-viewer ownership.

## Isolated desktop smoke

`gui_viewer_smoke.py` is an opt-in script for a **fresh Houdini process**.
It refuses a loaded HIP or preferences outside its scratch output directory.
It creates a floating panel in that process, clicks the actual Qt buttons,
yields to the UI, and records native state to JSON. It never loads/saves a
user scene. Its `RT_GUI_SMOKE_EXIT=1` option closes that isolated process.

Set the following only in the new child's environment:

- `RT_GUI_SMOKE_ISOLATED=1`
- `RT_GUI_SMOKE_OUTPUT=<scratch>/result.json`
- `HOUDINI_USER_PREF_DIR=<scratch>/prefs__HVER__`
- `HOUDINI_NO_ENV_FILE=1`
- `HOUDINI_PACKAGE_SKIP=1`
- `HOUDINI_PATH=<installed HFS>/houdini`
- `PXR_PLUGINPATH_NAME=<fresh build>/plugin_stage`
- `RT_TOGGLE_ROOT=<checkout>/extensions/rt_toggle`
- `RT_GUI_SMOKE_EXIT=1`

Launch the installed GUI executable with these arguments:

```text
houdini.exe -foreground waitforui <extension>/verification/gui_viewer_smoke.py
```

`waitforui` is documented in installed `ref.zip/commandline.txt`. Script
execution through that mechanism does not define `__file__`; the smoke uses
the explicit `RT_TOGGLE_ROOT`. This smoke bypasses package discovery, so it
does not constitute an installation/uninstallation test.

Require result `status: passed`, exact Houdini build, available renderers,
actual renderer/pause readback for each step, no panel script errors and
successful restoration. Keep build provenance, launch environment fields
(no credentials), stdout/stderr and the JSON together. Record failure paths
separately; do not overwrite earlier failed evidence with a successful run.

Pause and resume setters return before the native state changes in this
Houdini build. Poll to a bounded deadline while yielding to the event loop;
never reissue the setter while waiting. An immediate old pause flag is not a
failed operation. Keep both the pending request and observed state in evidence.

## Additional release checks

- Multiple viewers: change only the selected target, including after focus
  moves to another viewer. Check the panel's ambiguous initial state.
- Panel recreation: enable, recreate panel, then restore successfully.
- External changes: manually choose a different renderer; the panel follows
  it and Off cannot overwrite it. Re-enable captures the new prior renderer.
- Missing/closed viewer: no fallback to another viewer.
- Missing plugin or failed native call: readable error, truthful readback,
  and a retriable recovery path when restoration is partially complete.
- Shelf and radial action targeting; node Refresh/Enable direction, including
  node save/load without automatic activation.
- Normal installed preferences, menu/shelf discovery, scaling and uninstall.

The unit suite covers the decision logic. Desktop checks establish actual
HOM and Qt behavior only for the exact paths exercised and recorded.
