# RT Toggle — session hand-off (2026-11-12)

Latest main: **`v0.1.5`** release, tag on `e5f2111`. Published tags: `v0.1.5`,
`v0.1.4`, `v0.1.3`, `v0.1.2`, `v0.1.1`, `v0.1.0`. Release asset
`dist/houdini-rt-toggle-0.1.5.zip`, 60 members, 258,545 bytes, SHA-256
`8b4a139467a2c25e1356b91215f8c4364587dd95720facb851fa0fbcbb9a102c`.
Repo: `%HOUDINI_RT_ROOT%\extensions\rt_toggle`
Runtime tree: `%HOUDINI_RT_ROOT%` (repaired).

## What is proven live

- Houdini 22.0.429 / Python 3.13.10 hython launches the sidecar.
- Launcher `%HOUDINI_RT_ROOT%\python.exe` (3.14.2), package
  precedence **runtime-first**, non-degraded.
- `activate()` → ON; `/health` returns **200** with the RT pid/version.
- `status.json` reports `sys_prefix`/`sys_executable` at the HoudiniRT root and
  `entry_point: synapse.cognitive.agent_loop.run_turn`.
- The host invokes the entry point: with a guarded SYNAPSE client
  (`make_anthropic_client` + httpx MockTransport) and
  `Dispatcher(is_testing=True)`, the real agent loop returns
  `AgentTurnResult: complete`. No network call, no project content.
- Live guarded turn against `https://api.anthropic.com` returns `complete`
  (`RT_SIDECAR_LIVE_MODEL=1`, max_tokens 4096).
- Deactivate → OFF with a graceful stopped record; no orphan host.
- Runtime repair: core DLLs and site-packages binaries restored; RECORD audit
  798 hashes ok, 0 missing, 0 bad.
- Turn loop proven through the real loopback transport: `POST /turn` returns a
  terminal result, and an end-to-end smoke over the transport passes.

## What v0.1.2 added

- `rt_runtime/sidecar/turn_loop.py`: single-flight turn service. One turn at a
  time, `429` + `Retry-After: 1` on a second admission, cooperative
  `threading.Event` cancellation, 120 s soft deadline, bounded and retained
  results, daemon worker so shutdown never waits on a hung endpoint.
- `transport.py`: routes `/turn`, `/turn/<id>` and `/cancel`; 128 KiB body cap
  on both the normal and `Expect: 100-continue` paths; `TurnError` → HTTP
  status; turn fields merged into `/status` last.
- `host_stub.py`: the guarded `run_turn` entry is built in `--serve`, and the
  loop is shut down before the server stops.
- 32 new turn-loop tests and 16 new transport route tests.

## What v0.1.3 added

- `rt_runtime/turn_control.py`: `TurnControl` (`submit`/`status`/`cancel`,
  returning `(http_status, body)`) and `TurnPoller`, which follows a turn with
  no Qt and no Houdini. The token is read per call; a relaunch rotates it.
- `SidecarAdapter.turn_control()` returns a client only when a probe says
  `ready` and a token reads; `RuntimeAdapter.turn_control()` forwards it and
  returns `None` for in-process targets.
- The panel now submits a turn: prompt field, **Run turn**, **Cancel turn**, a
  250 ms `QTimer` poll, and a status label. `wait_ms=0`, so the UI never blocks.
  Offline by default; `RT_SIDECAR_LIVE_MODEL=1` runs a live turn.
- `rt_panel.pypanel` is regenerated from `rt_panel.py`; a test fails on drift.

## What v0.1.4 added

- Turn control on the shelf and the node: `turn_from_shelf()` and
  `cancel_turn_from_shelf()` in `rt_shelf.py`; **RT Turn** and **RT Turn
  Cancel** tools in `rt_shelf.shelf`; `rt_prompt`, Run Turn, Cancel Turn and a
  locked `rt_turn_status` on the node.
- Submission is `wait_ms=0` everywhere. The panel polls on a Qt timer; the shelf
  and node have no timer, so they poll for `RT_TURN_POLL_S` (default 2 s) and
  leave the turn running after that, reachable through cancel.
- 2 UI contract tests for the shelf tools and node parms.

## What v0.1.5 added

- **Icon family** in `houdini/icons/`: `rt_toggle.svg`, `rt_turn.svg`,
  `rt_turn_cancel.svg`, one family on a 32 px grid. The shelf tools and the
  panel tab point at them; the paths resolve through `RT_TOGGLE_ROOT`.
- **Package fix**: `houdini/packages/rt_toggle.json` now sets `RT_TOGGLE_ROOT`
  alongside `PYTHONPATH`, so a package install resolves the icons.
- **`docs/ARTIST_LAUNCH.md`**: the five native surfaces an artist looks at
  (shelf, viewer tab menu, HUD handles, radial menu, panel), the HUD recipe, and
  the documented limits.
- **`verification/GUI_CHECKS.md`**: renamed from `GUI_CHECKS_v0.1.4.md` because
  it outlives one release; it now holds ten steps, the tenth being HUD handles
  with an optional Vulkan sub-check.

## Counts

```
local Windows   3.12: 223 passed, 18 skipped
local Windows   3.14: 240 passed,  1 skipped
CI Ubuntu       3.12: 224 passed, 17 skipped
CI Ubuntu       3.13: 224 passed, 17 skipped
CI Windows      3.14: 238 passed,  3 skipped
```

CI run `43291688245` at `e5f2111` (tag) and `43291683130` (main) — all four jobs
success in both. The local/CI gap keeps its two causes: Ubuntu runs the POSIX
permission test that Windows skips, and the Windows runner has no `synapse`, so
the two guarded host-turn tests skip there. Each leg is 1 higher than at
`32480fa`: the icon contract test.

## Key paths

- Adapter: `rt_runtime/adapter.py`, `rt_runtime/sidecar_adapter.py`
- Host/supervisor/transport/turn loop:
  `rt_runtime/sidecar/{host_stub,lifecycle,transport,turn_loop}.py`
- Tests: `tests/` (240 on 3.14, 223 + skips on 3.12 locally)
- Icons: `houdini/icons/{rt_toggle,rt_turn,rt_turn_cancel}.svg`
- Artist launch: `docs/ARTIST_LAUNCH.md`
- GUI checks: `verification/GUI_CHECKS.md`
- Release audit: `tools/audit_release.py`
- Release build: `tools/build_release.py`
- Live config: `%TEMP%\rt-live-rtfirst\config.json` (RT-first + entry point)
- Sidecar state/logs: `%TEMP%\rt-live-rtfirst\state-5\`
- Integrity record: `%TEMP%\rt-sidecar\RUNTIME_INTEGRITY.md`

## Commands

```powershell
py -3.12 -m pytest tests -q -p no:cacheprovider
py -3.14 -m pytest tests -q -p no:cacheprovider
py -3.12 tools/build_release.py
py -3.12 tools/audit_release.py
```

Live check (spawns the 3.14 sidecar; stop it after):
```powershell
HOUDINI_USER_PREF_DIR=%TEMP%\rt-live-rtfirst\prefs22.0 `
HOUDINI_PACKAGE_SKIP=1 py -3.12 -m pytest ...   # or the hython runner in %TEMP%
```

## Open items

1. **Run the ten GUI steps.** `verification/GUI_CHECKS.md` is the single list:
   panel draw and guard, sidecar ON, offline turn with a responsive UI, cancel,
   the two shelf tools, the node parms, and HUD handles. None has been clicked
   in Houdini. The client, poller and server are tested over loopback and the
   wiring is checked statically; the clicks are the gap.
2. **Icon rendering and Vulkan.** The icon strings expand to real files; how
   they draw at shelf size and in the pane tab is unverified. HUD handles over
   the Vulkan viewport are undocumented in the reference corpus and must be
   recorded as NOT RUN until tested.
3. **Other GUI checks.** Pane render, parm event dispatch, gear-menu duplicate,
   uninstall (Install Check 6).
4. **3.13 in-process sibling runtime.** Planned, not installed; the ABI refusal
   remains the only in-process behaviour.
5. **Live-turn housekeeping.** Spend/credit monitoring, and keeping the
   per-state `model_access.json` in sync with whichever endpoint is in use. The
   default local proxy is `http://127.0.0.1:11434/v1/messages`; the real
   endpoint is `https://api.anthropic.com/v1/messages`.
6. **Release.** `v0.1.5` is the published release; `v0.1.0`–`v0.1.4` remain
   prior tags.
7. **Licensing.** TBD; no license file is included.

## Cautions

- Never activate the cp314 tree in-process; the ABI guard is correct.
- `%HOUDINI_RT_ROOT%\python.exe` without `-s` picks up user-site `rpds`/`pxr`; the sidecar
  launcher uses `-I`, which implies `-s`.
- SYNAPSE's vendored SDK carries cp311/cp313 binaries and is inactive on 3.14;
  the sidecar relies on installed packages. That warning is expected.
- The sidecar policy file is scoped to the state dir; do not confuse it with the
  global `%USERPROFILE%\SYNAPSE\.synapse\model_access.json`.
- Do not clobber the `v0.1.1` release asset. It matches its tag; that release's
  note explains the one document that lagged it.
