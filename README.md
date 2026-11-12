<p align="center">
  <img src="docs/rt-toggle-mark.svg" alt="RT Toggle" width="360">
</p>

# RT Toggle

**Houdini 22 runtime on/off for HoudiniRT — v0.1.2**

## The answer first

**Houdini 22.0.429 runs Python 3.13.10. HoudiniRT is a Python 3.14 distribution.**

The toggle does **not** load the 3.14 tree into Houdini's interpreter. It guards
it, refuses it, and runs it as a supervised sidecar instead.

**What works today:** sidecar ON is proven live — `/health` returns 200 and a
real `run_turn` returns complete. The in-process guard correctly refuses the
cp314 tree. A 3.13 in-process sibling runtime is planned, not installed.

**Tests:** 224 pass on Python 3.14; 207 pass with skips on 3.12.

---

## What works today

- **Sidecar mode.** `activate()` starts a CPython 3.14 process with runtime-first
  packages, waits for ready, checks `/health`, and reports ON. `deactivate()`
  stops it gracefully.
- **In-process guard.** Any attempt to load the full 3.14 `site-packages` under
  Houdini's 3.13 interpreter is refused with the ABI blocker named. Refusal is a
  designed outcome, not a crash.
- **Three surfaces.** A Python Panel (primary), a shelf tool (secondary), and an
  optional node. The viewport camera dropdown is not an extension point.
- **One adapter.** Every surface calls the same process-wide adapter, so they
  share one lock, one state file and one set of path bookkeeping.

---

## Install in 5 steps

Full detail, exact `hou` calls and uninstall are in **[INSTALL.md](INSTALL.md)**.

`%PREF%` = `hou.homeHoudiniDirectory()` (usually
`%USERPROFILE%\Documents\houdini22.0`). `%HOUDINI_RT_ROOT%` = the HoudiniRT
distribution root.

1. **Make it importable.** Set `HOUDINI_RT_ROOT` in `%PREF%\houdini.env`; copy
   `houdini/packages/rt_toggle.json` to `%PREF%\packages\`. Restart.
2. **Install the panel.** Copy `houdini/rt_panel.pypanel` to
   `%PREF%\python_panels\`. Keep the interface name `RT_Toggle`.
3. **Install the shelf.** Copy `rt_shelf.shelf` and `rt_shelf.py` together to
   `%PREF%\toolbar\`; set `RT_TOGGLE_ROOT` if the pair is copied out of the
   extension.
4. **Add the pane menu entry.** Copy `houdini/PaneTabTypeMenu.xml` to `%PREF%\`.
   It is a supplement; merge the single `<actionItem>` if one already exists.
5. **Open it.** Restart, choose **New Pane Tab Type → RT Toggle**, or click the
   **RT Toggle** shelf button.

**Never** put `%HOUDINI_RT_ROOT%\Lib\site-packages` on `PYTHONPATH`, in a package
JSON, in `houdini.env`, or in a startup script. The guard exists to keep it out.

---

## How it works

```mermaid
flowchart LR
  subgraph Surfaces
    P[Python Panel]
    S[Shelf tool]
    N[Node]
  end

  A[Adapter<br/>one lock · one state file]
  G{Compatibility guard}
  I[In-process<br/>matching interpreter]
  D[Sidecar<br/>Python 3.14 process]
  H[/health · status.json/]
  R[REFUSED<br/>not a crash]

  P --> A
  S --> A
  N --> A
  A --> G
  G -- safe --> I
  G -- cp314 under 3.13 --> R
  A -- mode: sidecar --> D
  D --> H
```

**Boundaries.** Surfaces never touch `sys.path`. The adapter owns state. The
guard makes the one safety decision. The sidecar is a separate process, not a
second interpreter inside Houdini.

## Activation

```mermaid
sequenceDiagram
  participant H as Houdini (3.13)
  participant A as rt_runtime
  participant S as Sidecar (3.14)
  participant API as /health

  H->>A: activate()
  A->>A: preflight + ABI guard
  alt in-process, cp314 under 3.13
    A-->>H: REFUSED · OFF · changed=false
  else sidecar mode, runtime-first
    A->>S: start supervisor
    S-->>A: status.json = ready
    A->>API: GET /health
    API-->>A: 200 · ok · pid · python 3.14
    A-->>H: ON
  end
```

---

## Runtime configuration

`rt_toggle.json` at the extension root names the target. Relative paths resolve
against that file's folder.

```json
{
  "version": 1,
  "default": "houdini_rt",
  "targets": {
    "houdini_rt": {
      "home": "../..",
      "python_version": "3.14",
      "site_packages": "../../Lib/site-packages",
      "allow_pure_python_only": false
    }
  }
}
```

Add `"mode": "sidecar"` plus a `sidecar` block to run the 3.14 process instead of
touching `sys.path`. The block carries the launcher, host script, state folder,
package precedence, timeouts and `entry_point`. See `DESIGN.md` for the full
field table.

**Package precedence.** `runtime-first` is the default and wins imports. Base-first
is available only with `"allow_degraded_base_first": true`, and every result is
labelled `DEGRADED`.

---

## ABI policy

`rt_runtime/compat.py` compares the host ABI with the target's declared Python and
scans filenames for ABI-specific binaries. It never imports what it inspects.

**Strict by default.** A target whose declared version differs from the host is
refused, even with no compiled files. Under Houdini's `cp313`, the default 3.14
target is refused with the mismatch named.

**Opt-in per target.** `"allow_pure_python_only": true` permits a mismatch only
when the scan finds no compiled modules, with a warning that 3.14-only syntax or
stdlib APIs may still fail.

---

## Sidecar: what ON means

- A supervised CPython 3.14 process, started with the runtime tree in front.
- A lock, a heartbeat and an atomically written `status.json`.
- A loopback HTTP control surface with a per-launch token: `/health`, `/status`,
  `/shutdown`, and a turn service — `POST /turn`, `GET /turn/<id>`,
  `POST /cancel`. One turn at a time, cooperative cancel, bounded results.
- `entry_point` reaches the host as `--entry-point`. The host invokes a no-arg
  target, a one-context target, or the `run_turn` shape with a guarded client and
  dispatcher.
- The default probe is offline. `RT_SIDECAR_LIVE_MODEL=1` uses the configured
  Anthropic endpoint and returns a real `AgentTurnResult`.

---

## Tests

```powershell
py -3.12 -m pytest tests -q -p no:cacheprovider
```

- **Python 3.12 (local, Windows):** 207 passed, 18 skipped (3.14-only sidecar
  lifecycle tests, plus the POSIX permission test).
- **Python 3.14 (local, Windows):** 224 passed, 1 skipped (the POSIX
  permission test).
- **CI, Ubuntu:** 208 passed, 17 skipped on both 3.12 and 3.13 — the POSIX
  permission test runs there instead of skipping.
- **CI, Windows:** 222 passed, 3 skipped on 3.14 — `synapse` is not installed on
  the runner, so the two guarded host-turn tests skip as well as the POSIX one.

The local and CI counts differ for host reasons, not code reasons: local runs
have `synapse` available, and Windows cannot exercise POSIX permission bits.

The suite needs no Houdini; `tests/fake_hou.py` stands in for `hou`. CI runs
3.12/3.13 on Ubuntu and the full suite on 3.14 on Windows.

---

## Proven vs untested

**Proven live in hython 22.0.429:**

- The panel installs; `interfaceByName("RT_Toggle")` resolves.
- The cp314 target is refused: `safe=false`, 104 blockers, no cp314 import.
- The shelf XSD validates; the tool script reports one refusal line.
- The node builds and its callback function writes REFUSED and reverts.
- The state file survives restart, reports ERROR, re-activates, and deactivates.
- The pane-menu supplement has one item at the right anchor.
- RT-first sidecar: ON, `/health` 200, `run_turn` complete, graceful OFF.

**Manual, not machine-verified:**

- Pane render, shelf click, parm event dispatch, gear-menu duplicate and uninstall.
- The planned 3.13 in-process sibling runtime (G8) — no tree has been installed.
- A network model turn without credentials; the offline probe is the default.

`verification/MATRIX.md` and `INSTALL_CHECKS.md` keep the per-check status.

---

## Layout

```
rt_toggle/
├─ README.md
├─ INSTALL.md · DESIGN.md · CHANGELOG.md
├─ rt_toggle.json
├─ rt_runtime/
│  ├─ adapter.py · compat.py · session.py
│  ├─ sidecar_adapter.py
│  └─ sidecar/                host, supervisor, loopback transport
├─ houdini/                   panel, shelf, node, pane menu, package
├─ docs/
│  ├─ CLAIMS_VS_REFERENCE.md
│  └─ rt-toggle-mark.svg
├─ tests/
└─ verification/
```

The state file `.rt_toggle_state.json` is generated at runtime and is not tracked.

## License

License: TBD. No license file is included; licensing is the owner's decision.

---

*RT Toggle v0.1.2 · Myth and Logic*
