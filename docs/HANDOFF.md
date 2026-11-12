# RT Toggle — session hand-off (2026-11-12)

Latest main: **`v0.1.2`** release, tag on `d3e2bc8` (sidecar turn loop). The
commits after it, `b0e9c02` and this one, are documentation only.
Repo: `%HOUDINI_RT_ROOT%\extensions\rt_toggle`
Runtime tree: `%HOUDINI_RT_ROOT%` (repaired).
Release asset: `dist/houdini-rt-toggle-0.1.2.zip`, 50 members, 228,170 bytes,
SHA-256 `2c37b72a768285bd174b6dde552459905b0fec5043d797530524b6add731afdc`.

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

## Counts

```
local Windows   3.12: 207 passed, 18 skipped, 18 subtests
local Windows   3.14: 224 passed,  1 skipped
CI Ubuntu       3.12: 208 passed, 17 skipped
CI Ubuntu       3.13: 208 passed, 17 skipped
CI Windows      3.14: 222 passed,  3 skipped
```

CI run `43238913154` at `b0e9c02` — all four jobs success. Local and CI differ
by host: Ubuntu runs the POSIX permission test that Windows skips, and the
Windows runner has no `synapse`, so the two guarded host-turn tests skip there.

## Key paths

- Adapter: `rt_runtime/adapter.py`, `rt_runtime/sidecar_adapter.py`
- Host/supervisor/transport/turn loop:
  `rt_runtime/sidecar/{host_stub,lifecycle,transport,turn_loop}.py`
- Tests: `tests/` (224 on 3.14, 207 + skips on 3.12 locally)
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

1. **A caller for the turn loop.** `POST /turn` works and is tested, but no
   surface submits a turn: the panel and shelf still call the adapter, and
   SYNAPSE does not call the route. The next step is one client, then a UI
   control that uses it.
2. **GUI checks.** Pane render, shelf click, parm event dispatch, gear-menu
   duplicate, uninstall (Install Check 6) are still manual.
3. **3.13 in-process sibling runtime.** Planned, not installed; the ABI refusal
   remains the only in-process behaviour.
4. **Live-turn housekeeping.** Spend/credit monitoring, and keeping the
   per-state `model_access.json` in sync with whichever endpoint is in use. The
   default local proxy is `http://127.0.0.1:11434/v1/messages`; the real
   endpoint is `https://api.anthropic.com/v1/messages`.
5. **Release.** `v0.1.2` is the published release (turn loop); `v0.1.1` and
   `v0.1.0` remain prior tags.
6. **Licensing.** TBD; no license file is included.

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
